from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.config import (
    DeploymentMode,
    detect_deployment_mode,
    kite_configuration_status,
    load_runtime_config,
    mask_secret,
    telegram_configuration_status,
)
from momentum_edge.kite_client import KiteAuthenticationError, KiteCredentials
from momentum_edge.options import SelectionStatus, empty_option_state
from momentum_edge.storage import PERSISTENCE_MODE, ensure_data_directory, persistence_file_status, project_root, runtime_data_dir
from momentum_edge.version import APP_VERSION


class DeploymentTest(unittest.TestCase):
    def test_linux_safe_path_construction(self) -> None:
        status = ensure_data_directory(Path("data"))

        self.assertNotIn("\\", str(status.path))

    def test_runtime_directory_creation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime" / "data"
            status = ensure_data_directory(path)

            self.assertTrue(status.exists)
            self.assertTrue(status.writable)

    def test_unavailable_data_directory_reports_not_writable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "not_a_directory"
            file_path.write_text("occupied", encoding="utf-8")

            status = ensure_data_directory(file_path)

            self.assertTrue(status.exists)
            self.assertFalse(status.writable)
            self.assertIsNotNone(status.error)

    def test_project_root_discovery(self) -> None:
        self.assertEqual(project_root(), Path(__file__).resolve().parents[1])

    def test_environment_variable_configuration(self) -> None:
        config = load_runtime_config(
            environ={"KITE_API_KEY": "abc", "KITE_API_SECRET": "secret", "KITE_ACCESS_TOKEN": "xyz", "APP_ENV": "cloud"},
            streamlit_secrets={},
        )

        self.assertEqual(config.secrets["KITE_API_KEY"], "abc")
        self.assertEqual(config.secrets["KITE_API_SECRET"], "secret")
        self.assertEqual(config.secrets["KITE_ACCESS_TOKEN"], "xyz")
        self.assertEqual(config.app_env, "cloud")

    def test_streamlit_secrets_configuration_adapter(self) -> None:
        config = load_runtime_config(
            environ={},
            streamlit_secrets={"KITE_API_KEY": "cloud", "KITE_API_SECRET": "secret", "KITE_ACCESS_TOKEN": "token"},
        )

        self.assertEqual(config.secrets["KITE_API_KEY"], "cloud")
        self.assertTrue(kite_configuration_status(config)["configured"])

    def test_data_dir_configuration(self) -> None:
        config = load_runtime_config(environ={"DATA_DIR": "runtime-data"}, streamlit_secrets={})

        self.assertEqual(config.data_dir, Path("runtime-data"))
        self.assertEqual(runtime_data_dir(config), project_root() / "runtime-data")

    def test_missing_credentials(self) -> None:
        old_api = os.environ.pop("KITE_API_KEY", None)
        old_token = os.environ.pop("KITE_ACCESS_TOKEN", None)
        try:
            with self.assertRaises(KiteAuthenticationError):
                KiteCredentials.from_environment()
        finally:
            if old_api is not None:
                os.environ["KITE_API_KEY"] = old_api
            if old_token is not None:
                os.environ["KITE_ACCESS_TOKEN"] = old_token

    def test_secret_masking(self) -> None:
        self.assertEqual(mask_secret(None), "missing")
        self.assertEqual(mask_secret("abcd"), "configured")
        self.assertEqual(mask_secret("abcdef"), "ab***ef")

    def test_deployment_mode_detection(self) -> None:
        self.assertEqual(detect_deployment_mode({}), DeploymentMode.LOCAL)
        self.assertEqual(detect_deployment_mode({"STREAMLIT_CLOUD": "1"}), DeploymentMode.STREAMLIT_CLOUD)
        self.assertEqual(detect_deployment_mode({"APP_ENV": "cloud"}), DeploymentMode.STREAMLIT_CLOUD)
        self.assertEqual(
            detect_deployment_mode({"MOMENTUM_EDGE_DEPLOYMENT_MODE": "STREAMLIT_CLOUD"}),
            DeploymentMode.STREAMLIT_CLOUD,
        )

    def test_app_version(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.0-cloud-preview")

    def test_telegram_configuration_status(self) -> None:
        missing = telegram_configuration_status(load_runtime_config(environ={}, streamlit_secrets={}))
        configured = telegram_configuration_status(
            load_runtime_config(environ={"TELEGRAM_BOT_TOKEN": "bot", "TELEGRAM_CHAT_IDS": "1,2"}, streamlit_secrets={})
        )

        self.assertFalse(missing["configured"])
        self.assertTrue(configured["configured"])

    def test_kite_configuration_requires_secret_for_live_readiness(self) -> None:
        config = load_runtime_config(environ={"KITE_API_KEY": "key", "KITE_ACCESS_TOKEN": "token"}, streamlit_secrets={})

        self.assertFalse(kite_configuration_status(config)["configured"])

    def test_persistence_file_status_reports_missing_empty_and_present(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "alert_history.json").write_text("", encoding="utf-8")
            (data_dir / "signal_lifecycle.json").write_text("{}", encoding="utf-8")

            status = persistence_file_status(data_dir)

            self.assertEqual(PERSISTENCE_MODE, "TEMPORARY LOCAL FILES")
            self.assertEqual(status["alert_history.json"]["state"], "empty")
            self.assertEqual(status["signal_lifecycle.json"]["state"], "present")
            self.assertEqual(status["signal_outcomes.json"]["state"], "missing")

    def test_option_selection_empty_state_is_cloud_safe_without_live_data(self) -> None:
        state = empty_option_state()

        self.assertEqual(state.recommendations, [])
        self.assertEqual(state.keys, [])
        self.assertEqual(SelectionStatus.NO_SUITABLE_CONTRACT.value, "NO_SUITABLE_CONTRACT")

    def test_importability_without_manual_pythonpath(self) -> None:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", "import momentum_edge; print('ok')"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
