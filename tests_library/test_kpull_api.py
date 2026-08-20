import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ble.kinvent.kpull.acquisition_service import KPullAcquisitionService


FIELDS = [
    "timestamp_utc",
    "sensor_time",
    "raw_force",
    "tare_offset",
    "force_counts",
    "force_kg",
    "force_n",
]


class KPullApiTest(unittest.TestCase):
    def make_ready_service(self, directory):
        service = KPullAcquisitionService()
        root = Path(directory)
        service._live_path = root / "live.csv"
        service._log_path = root / "worker.log"
        service._control_path = root / "control.json"
        service._log_path.write_text(
            "K-Pull prêt; liaison Bluetooth conservée.\n",
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
                ["2026-06-22T07:00:01+00:00", 1, 1, 0, 1, 6, 58.86]
            )
            writer.writerow(
                ["2026-06-22T07:00:02+00:00", 2, 2, 0, 2, 12, 117.72]
            )

    def test_connect_requests_kpull_from_unique_manager(self):
        service = KPullAcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._live_path = root / "live.csv"
            service._log_path = root / "worker.log"
            service._control_path = root / "control.json"
            with mock.patch(
                "ble.kinvent.kpull.acquisition_service.request_sensor"
            ) as request, mock.patch(
                "ble.kinvent.kpull.acquisition_service.ManagedSensorProcess",
                return_value=process,
            ):
                status = service.connect()

        request.assert_called_once_with("kpull")
        self.assertTrue(status["connected"])

    def test_ignores_stale_idle_manager_state(self):
        service = KPullAcquisitionService()
        service._recording = True
        service._armed = True
        service._sensor_ready = True
        with mock.patch(
            "ble.kinvent.kpull.acquisition_service.manager_state",
            return_value={
                "target": "kpull",
                "phase": "idle",
                "generation": "old",
                "pid": 123,
            },
        ):
            status = service.status()

        self.assertFalse(status["connected"])
        self.assertFalse(status["running"])
        self.assertEqual(status["phase"], "disconnected")

    def test_reports_tare_then_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            service._log_path.write_text(
                "Tare K-Pull pendant 2.0 s\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "tare")
            service._log_path.write_text(
                "K-Pull prêt; liaison Bluetooth conservée.\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "ready")

    def test_start_requires_connected_kpull(self):
        service = KPullAcquisitionService()
        with self.assertRaisesRegex(RuntimeError, "Connectez le K-Pull"):
            service.start()

    def test_latest_reports_force_and_recording_maximum(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-22T07:00:00+00:00"
            service._recording = True
            service._duration = 1_000_000_000
            service._csv_path = Path(directory) / "test.csv"

            latest = service.latest()

        self.assertEqual(latest["phase"], "active")
        self.assertIsNone(latest["battery_pct"])
        self.assertIsNone(latest["measurement"]["battery_pct"])
        self.assertEqual(latest["measurement"]["force_kg"], 12.0)
        self.assertEqual(latest["measurement"]["max_force_n"], 117.72)
        self.assertAlmostEqual(latest["measurement"]["max_force_kg"], 12.0)

    def test_start_arms_then_first_traction_starts_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)

            status = service.start(duration=1_000_000_000, filename="armed.csv")
            initial_control = service._control_path.read_text()
            self.assertFalse(status["running"])
            self.assertEqual(status["phase"], "armed")
            self.assertIn('"action": "start"', initial_control)

            self.write_live_rows(service._live_path)
            latest = service.latest()
            control = service._control_path.read_text()

        self.assertTrue(latest["running"])
        self.assertEqual(latest["phase"], "active")
        self.assertEqual(latest["started_at"], "2026-06-22T07:00:02+00:00")
        self.assertIn('"action": "start"', control)

    def test_stop_keeps_kpull_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-22T07:00:00+00:00"
            service._recording = True
            service._csv_path = Path(directory) / "test.csv"

            status = service.stop()
            control = service._control_path.read_text()

        self.assertFalse(status["running"])
        self.assertTrue(status["connected"])
        self.assertEqual(status["phase"], "ready")
        self.assertIn('"action": "stop"', control)


if __name__ == "__main__":
    unittest.main()
