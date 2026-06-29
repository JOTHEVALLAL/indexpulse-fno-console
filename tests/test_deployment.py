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
)
from momentum_edge.kite_client import KiteAuthenticationError, KiteCredentials
from momentum_edge.storage import ensure_data_directory
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

    def test_environment_variable_configuration(self) -> None:
        config = load_runtime_config(
            environ={"KITE_API_KEY": "abc", "KITE_ACCESS_TOKEN": "xyz"},
            streamlit_secrets={},
        )

        self.assertEqual(config.secrets["KITE_API_KEY"], "abc")
        self.assertEqual(config.secrets["KITE_ACCESS_TOKEN"], "xyz")

    def test_streamlit_secrets_configuration_adapter(self) -> None:
        config = load_runtime_config(environ={}, streamlit_secrets={"KITE_API_KEY": "cloud", "KITE_ACCESS_TOKEN": "token"})

        self.assertEqual(config.secrets["KITE_API_KEY"], "cloud")
        self.assertTrue(kite_configuration_status(config)["configured"])

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
        self.assertEqual(
            detect_deployment_mode({"MOMENTUM_EDGE_DEPLOYMENT_MODE": "STREAMLIT_CLOUD"}),
            DeploymentMode.STREAMLIT_CLOUD,
        )

    def test_app_version(self) -> None:
        self.assertEqual(APP_VERSION, "0.3.0-cloud-preview")

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
