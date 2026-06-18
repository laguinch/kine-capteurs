import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ble.kinvent.kplates.acquisition_service as acquisition_module
from ble.kinvent.kplates.acquisition_service import DualPlateAcquisitionService


class KPlateApiTest(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_routes_are_registered(self):
        from app.main import app

        paths = {route.path for route in app.routes}

        self.assertIn("/api/kplates/dual/start", paths)
        self.assertIn("/api/kplates/dual/stop", paths)
        self.assertIn("/api/kplates/dual/status", paths)
        self.assertIn("/api/kplates/dual/latest", paths)
        self.assertIn("/api/kplates/dual/download", paths)

    def test_new_service_is_idle(self):
        status = DualPlateAcquisitionService().status()

        self.assertFalse(status["running"])
        self.assertIsNone(status["pid"])
        self.assertIsNone(status["csv_path"])

    def test_latest_reads_last_complete_measurement(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dual.csv"
            path.write_text(
                "timestamp_utc,sync_delta_ms,left_kg,right_kg,total_kg\n"
                "2026-06-18T08:00:00+00:00,12.5,50.0,55.0,105.0\n",
                encoding="utf-8",
            )
            service._csv_path = path

            latest = service.latest()

        measurement = latest["measurement"]
        self.assertEqual(measurement["left_kg"], 50.0)
        self.assertEqual(measurement["right_kg"], 55.0)
        self.assertEqual(measurement["total_kg"], 105.0)

    def test_command_prefix_supports_shell_quoting(self):
        service = DualPlateAcquisitionService()
        original = os.environ.get("KINE_HCI_COMMAND_PREFIX")
        try:
            os.environ["KINE_HCI_COMMAND_PREFIX"] = "sudo -n"
            self.assertEqual(
                service._convert_measurement({"left_kg": "50"})["left_kg"],
                50.0,
            )
        finally:
            if original is None:
                os.environ.pop("KINE_HCI_COMMAND_PREFIX", None)
            else:
                os.environ["KINE_HCI_COMMAND_PREFIX"] = original

    def test_start_adds_csv_extension(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(pid=123)
            process.poll.return_value = None
            with (
                mock.patch.object(acquisition_module, "BASE_DIR", Path(directory)),
                mock.patch.object(
                    acquisition_module.subprocess,
                    "Popen",
                    return_value=process,
                ),
            ):
                status = service.start(filename="session")

        self.assertTrue(status["csv_path"].endswith("/session.csv"))

    def test_failed_process_reports_last_log_line(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "dual.csv"
            csv_path.with_suffix(".log").write_text(
                "Initialisation\nsudo: a password is required\n",
                encoding="utf-8",
            )
            service._csv_path = csv_path
            service._process = subprocess.Popen(
                [
                    os.environ.get("PYTHON", "python3"),
                    "-c",
                    "raise SystemExit(1)",
                ]
            )
            service._process.wait()

            status = service.status()

        self.assertIn("code 1", status["last_error"])
        self.assertIn("sudo: a password is required", status["last_error"])


if __name__ == "__main__":
    unittest.main()
