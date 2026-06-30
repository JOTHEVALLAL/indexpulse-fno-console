from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Mapping


class DeploymentMode(str, Enum):
    LOCAL = "LOCAL"
    STREAMLIT_CLOUD = "STREAMLIT_CLOUD"


SECRET_KEYS = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "KITE_REDIRECT_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS",
    "APP_ENV",
    "DATA_DIR",
)


@dataclass(frozen=True)
class RuntimeConfig:
    deployment_mode: DeploymentMode
    secrets: Mapping[str, str]
    app_env: str | None = None
    data_dir: Path | None = None


def detect_deployment_mode(environ: Mapping[str, str] | None = None) -> DeploymentMode:
    env = environ or os.environ
    explicit = env.get("MOMENTUM_EDGE_DEPLOYMENT_MODE")
    if explicit in {mode.value for mode in DeploymentMode}:
        return DeploymentMode(explicit)
    app_env = env.get("APP_ENV", "").lower()
    if app_env in {"cloud", "streamlit_cloud", "streamlit-cloud", "production"}:
        return DeploymentMode.STREAMLIT_CLOUD
    if env.get("STREAMLIT_CLOUD") == "1" or env.get("STREAMLIT_RUNTIME") == "cloud":
        return DeploymentMode.STREAMLIT_CLOUD
    return DeploymentMode.LOCAL


def _streamlit_secrets() -> Mapping[str, str]:
    try:
        streamlit = import_module("streamlit")
        return dict(getattr(streamlit, "secrets", {}))
    except Exception:
        return {}


def load_secrets(
    environ: Mapping[str, str] | None = None,
    streamlit_secrets: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = environ or os.environ
    st_secrets = _streamlit_secrets() if streamlit_secrets is None else streamlit_secrets
    secrets: dict[str, str] = {}
    for key in SECRET_KEYS:
        value = env.get(key) or st_secrets.get(key)
        if value:
            secrets[key] = str(value)
    return secrets


def load_runtime_config(
    environ: Mapping[str, str] | None = None,
    streamlit_secrets: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    secrets = load_secrets(environ, streamlit_secrets)
    data_dir = Path(secrets["DATA_DIR"]).expanduser() if secrets.get("DATA_DIR") else None
    return RuntimeConfig(
        deployment_mode=detect_deployment_mode(environ),
        secrets=secrets,
        app_env=secrets.get("APP_ENV"),
        data_dir=data_dir,
    )


def secret_value(key: str, config: RuntimeConfig | None = None) -> str | None:
    runtime = config or load_runtime_config()
    return runtime.secrets.get(key)


def mask_secret(value: str | None) -> str:
    if not value:
        return "missing"
    if len(value) <= 4:
        return "configured"
    return f"{value[:2]}***{value[-2:]}"


def kite_configuration_status(config: RuntimeConfig | None = None) -> dict[str, str | bool]:
    runtime = config or load_runtime_config()
    required = ("KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN")
    return {
        "configured": all(key in runtime.secrets for key in required),
        "api_key": "configured" if "KITE_API_KEY" in runtime.secrets else "missing",
        "access_token": "configured" if "KITE_ACCESS_TOKEN" in runtime.secrets else "missing",
        "api_secret": "configured" if "KITE_API_SECRET" in runtime.secrets else "missing",
        "redirect_url": "configured" if "KITE_REDIRECT_URL" in runtime.secrets else "missing",
    }


def telegram_configuration_status(config: RuntimeConfig | None = None) -> dict[str, str | bool]:
    runtime = config or load_runtime_config()
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS")
    return {
        "configured": all(key in runtime.secrets for key in required),
        "bot_token": "configured" if "TELEGRAM_BOT_TOKEN" in runtime.secrets else "missing",
        "chat_ids": "configured" if "TELEGRAM_CHAT_IDS" in runtime.secrets else "missing",
    }


def package_import_status() -> dict[str, str]:
    status = {}
    for module_name in ("streamlit", "kiteconnect", "momentum_edge"):
        try:
            module = import_module(module_name)
            status[module_name] = getattr(module, "__version__", "importable")
        except Exception as exc:
            status[module_name] = f"unavailable: {exc.__class__.__name__}"
    status["python"] = sys.version.split()[0]
    return status
