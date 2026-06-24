import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ble.kinvent.kpush.acquisition_service import KPushAcquisitionService


FIELDS = [
    "timestamp_utc",
    "sensor_time",
    "raw_force",
    "tare_offset",
    "force_counts",
    "force_kg",
    "force_n",
]


class KPushApiTest(unittest.TestCase):
    def make_ready_service(self, directory):
        service = KPushAcquisitionService()
        root = Path(directory)
        service._live_path = root / "live.csv"
        service._log_path = root / "worker.log"
        service._control_path = root / "control.json"
        service._log_path.write_text(
            "K-Push prêt; liaison Bluetooth conservée.\n",
            encoding="utf-8",
        )
        service._process = mock.Mock(pid=123)
        service._process.poll.return_value = None
        return service

    def write_live_rows(self, path):
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(FIELDS)
            writer.writerow(
                ["2026-06-19T15:00:01+00:00", 1, 1, 0, 1, 10, 98.1]
            )
            writer.writerow(
                ["2026-06-19T15:00:02+00:00", 2, 2, 0, 2, 20, 196.2]
            )

    def test_connect_requests_kpush_from_unique_manager(self):
        service = KPushAcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._live_path = root / "live.csv"
            service._log_path = root / "worker.log"
            service._control_path = root / "control.json"
            with mock.patch(
                "ble.kinvent.kpush.acquisition_service.request_sensor"
            ) as request, mock.patch(
                "ble.kinvent.kpush.acquisition_service.ManagedSensorProcess",
                return_value=process,
            ):
                status = service.connect()

        request.assert_called_once_with("kpush")
        self.assertTrue(status["connected"])
        self.assertFalse(status["running"])

    def test_reports_connection_and_tare_phases_from_worker_log(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            service._log_path.write_text(
                "Connexion LE directe...\nTare K-Push pendant 2.0 s\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "tare")
            service._log_path.write_text(
                "K-Push prêt; liaison Bluetooth conservée.\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "ready")
            service._log_path.write_text("mesures récentes seulement\n")
            self.assertEqual(service.status()["phase"], "ready")

    def test_start_requires_ready_connected_sensor(self):
        service = KPushAcquisitionService()

        with self.assertRaisesRegex(RuntimeError, "Connectez le K-Push"):
            service.start()

    def test_latest_reports_force_and_recording_maximum(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-19T15:00:00+00:00"
            service._recording = True
            service._duration = 1_000_000
            service._csv_path = Path(directory) / "test.csv"

            latest = service.latest()

        self.assertEqual(latest["phase"], "active")
        self.assertEqual(latest["measurement"]["force_n"], 196.2)
        self.assertEqual(latest["measurement"]["max_force_n"], 196.2)
        self.assertAlmostEqual(latest["measurement"]["max_force_kg"], 20.0)

    def test_start_arms_then_first_force_starts_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)

            status = service.start(duration=1_000_000_000, filename="armed.csv")
            self.assertFalse(status["running"])
            self.assertEqual(status["phase"], "armed")

            self.write_live_rows(service._live_path)
            latest = service.latest()
            control = service._control_path.read_text()

        self.assertTrue(latest["running"])
        self.assertEqual(latest["phase"], "active")
        self.assertEqual(latest["started_at"], "2026-06-19T15:00:02+00:00")
        self.assertIn('"action": "start"', control)

    def test_stop_keeps_kpush_connected_and_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-19T15:00:00+00:00"
            service._recording = True
            service._csv_path = Path(directory) / "test.csv"

            status = service.stop()
            control = service._control_path.read_text()

        self.assertFalse(status["running"])
        self.assertTrue(status["connected"])
        self.assertEqual(status["phase"], "ready")
        self.assertIn('"action": "stop"', control)

    def test_stop_changes_state_before_final_csv_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            service._started_at = "2026-06-19T15:00:00+00:00"
            service._recording = True
            service._csv_path = Path(directory) / "test.csv"
            recording_states = []
            service._write_recording_csv = lambda: recording_states.append(
                service._recording
            )

            service.stop()

        self.assertEqual(recording_states, [False])


if __name__ == "__main__":
    unittest.main()
