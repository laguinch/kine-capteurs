import importlib.util
import json
import os
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

        paths = set(app.openapi()["paths"])

        self.assertIn("/api/kplates/dual/start", paths)
        self.assertIn("/api/kplates/dual/stop", paths)
        self.assertIn("/api/kplates/dual/connect", paths)
        self.assertIn("/api/kplates/dual/disconnect", paths)
        self.assertIn("/api/kplates/dual/status", paths)
        self.assertIn("/api/kplates/dual/latest", paths)
        self.assertIn("/api/kplates/dual/download", paths)

    def test_new_service_is_idle(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            service._worker_state_path = Path(directory) / "missing-state.json"
            status = service.status()

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
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._worker_state_path.write_text(
                f'{{"phase":"idle","pid":{os.getpid()}}}',
                encoding="utf-8",
            )
            with mock.patch.object(acquisition_module, "BASE_DIR", root):
                status = service.start(filename="session")

        self.assertTrue(status["csv_path"].endswith("/session.csv"))

    def test_worker_error_is_reported(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            service._worker_state_path = Path(directory) / "state.json"
            service._worker_state_path.write_text(
                (
                    '{"phase":"error","pid":'
                    f'{os.getpid()},"error":"Connexion Bluetooth impossible"}}'
                ),
                encoding="utf-8",
            )

            status = service.status()

        self.assertEqual(status["last_error"], "Connexion Bluetooth impossible")

    def test_worker_error_clears_pending_acquisition(self):
        service = DualPlateAcquisitionService()
        service._generation = "pending-test"
        with tempfile.TemporaryDirectory() as directory:
            service._worker_state_path = Path(directory) / "state.json"
            service._worker_state_path.write_text(
                (
                    f'{{"phase":"error","pid":{os.getpid()},'
                    '"generation":"pending-test","error":"Flux absent"}'
                ),
                encoding="utf-8",
            )

            status = service.status()

        self.assertFalse(status["running"])
        self.assertEqual(status["last_error"], "Flux absent")

    def test_second_test_reuses_persistent_bluetooth_process(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._worker_state_path.write_text(
                f'{{"phase":"idle","pid":{os.getpid()}}}',
                encoding="utf-8",
            )
            with mock.patch.object(acquisition_module, "BASE_DIR", root):
                status = service.start(filename="second-test.csv")

            control = json.loads(service._control_path.read_text(encoding="utf-8"))

        self.assertTrue(status["running"])
        self.assertEqual(control["action"], "start")

    def test_detects_external_persistent_worker(self):
        service = DualPlateAcquisitionService()

        with mock.patch.object(
            acquisition_module.os,
            "kill",
            side_effect=PermissionError,
        ):
            self.assertTrue(service._worker_alive(123))

    def test_stop_only_commands_recording_to_stop(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._worker_state_path.write_text(
                (
                    f'{{"phase":"active","pid":{os.getpid()},'
                    '"generation":"test-generation"}'
                ),
                encoding="utf-8",
            )

            service.stop()
            command = json.loads(
                service._control_path.read_text(encoding="utf-8")
            )

        self.assertEqual(command["action"], "stop")
        self.assertEqual(command["generation"], "test-generation")

    def test_disconnect_only_commands_radio_links_to_close(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._worker_state_path.write_text(
                f'{{"phase":"idle","pid":{os.getpid()}}}',
                encoding="utf-8",
            )

            service.disconnect()
            command = json.loads(
                service._control_path.read_text(encoding="utf-8")
            )

        self.assertEqual(command["action"], "disconnect")

    def test_connect_commands_disconnected_worker_to_reconnect(self):
        service = DualPlateAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._worker_state_path.write_text(
                f'{{"phase":"disconnected","pid":{os.getpid()}}}',
                encoding="utf-8",
            )

            service.connect()
            command = json.loads(
                service._control_path.read_text(encoding="utf-8")
            )

        self.assertEqual(command["action"], "connect")
        self.assertTrue(command["generation"])

    def test_reconnection_generation_is_not_an_acquisition(self):
        service = DualPlateAcquisitionService()
        service._generation = "old-test"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._control_path = root / "control.json"
            service._worker_state_path = root / "state.json"
            service._control_path.write_text(
                '{"action":"connect","generation":"reconnect"}',
                encoding="utf-8",
            )
            service._worker_state_path.write_text(
                (
                    f'{{"phase":"idle","pid":{os.getpid()},'
                    '"generation":"reconnect"}'
                ),
                encoding="utf-8",
            )

            status = service.status()

        self.assertFalse(status["running"])
        self.assertTrue(status["bluetooth_connected"])


if __name__ == "__main__":
    unittest.main()
