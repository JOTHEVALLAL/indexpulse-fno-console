from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import RuntimeConfig, load_runtime_config

PERSISTENCE_MODE = "TEMPORARY LOCAL FILES"
PERSISTENCE_FILENAMES = (
    "alert_history.json",
    "signal_lifecycle.json",
    "signal_outcomes.json",
    "option_recommendations.json",
    "market_diagnostics.csv",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_data_dir(config: RuntimeConfig | None = None) -> Path:
    runtime = config or load_runtime_config()
    data_dir = runtime.data_dir or Path("data")
    if data_dir.is_absolute():
        return data_dir
    return project_root() / data_dir


DATA_DIR = runtime_data_dir()


@dataclass(frozen=True)
class DataDirectoryStatus:
    path: Path
    exists: bool
    writable: bool
    error: str | None = None


def ensure_data_directory(path: Path | str | None = None) -> DataDirectoryStatus:
    data_path = Path(path) if path is not None else runtime_data_dir()
    try:
        data_path.mkdir(parents=True, exist_ok=True)
        probe = data_path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return DataDirectoryStatus(path=data_path, exists=True, writable=True)
    except OSError as exc:
        return DataDirectoryStatus(path=data_path, exists=data_path.exists(), writable=False, error=str(exc))


def persistence_file_status(data_dir: Path | str | None = None) -> dict[str, dict[str, str | int | bool]]:
    base = Path(data_dir) if data_dir is not None else runtime_data_dir()
    statuses: dict[str, dict[str, str | int | bool]] = {}
    for filename in PERSISTENCE_FILENAMES:
        path = base / filename
        try:
            exists = path.exists()
            size = path.stat().st_size if exists else 0
            state = "missing"
            if exists:
                state = "empty" if size == 0 else "present"
            statuses[filename] = {
                "path": str(path),
                "exists": exists,
                "size_bytes": size,
                "state": state,
            }
        except OSError as exc:
            statuses[filename] = {
                "path": str(path),
                "exists": False,
                "size_bytes": 0,
                "state": "unavailable",
                "error": str(exc),
            }
    return statuses
