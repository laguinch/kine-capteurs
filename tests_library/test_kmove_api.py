import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ble.kinvent.kmove.acquisition_service import KMoveAcquisitionService


FIELDS = [
    "timestamp_utc",
    "sensor_time",
    "quaternion_w",
    "quaternion_x",
    "quaternion_y",
    "quaternion_z",
    "rotation_x_deg",
    "rotation_y_deg",
    "rotation_z_deg",
    "accel_x_raw",
    "accel_y_raw",
    "accel_z_raw",
    "battery_pct",
]


class KMoveApiTest(unittest.TestCase):
    def make_ready_service(self, directory):
        service = KMoveAcquisitionService()
        root = Path(directory)
        service._live_path = root / "live.csv"
        service._log_path = root / "worker.log"
        service._control_path = root / "control.json"
        service._log_path.write_text(
            "K-Move prêt; liaison Bluetooth conservée.\n",
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
                [
                    "2026-06-22T08:00:01+00:00", 1,
                    1, 0, 0, 0, -20, 35, -10, 0, 0, 0, 60,
                ]
            )
            writer.writerow(
                [
                    "2026-06-22T08:00:02+00:00", 2,
                    1, 0, 0, 0, 65, -40, 15, 0, 0, 0, 59,
                ]
            )

    def test_connect_requests_kmove_from_unique_manager(self):
        service = KMoveAcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._live_path = root / "live.csv"
            service._log_path = root / "worker.log"
            service._control_path = root / "control.json"
            with mock.patch(
                "ble.kinvent.kmove.acquisition_service.request_sensor"
            ) as request, mock.patch(
                "ble.kinvent.kmove.acquisition_service.ManagedSensorProcess",
                return_value=process,
            ):
                status = service.connect()

        request.assert_called_once_with("kmove")
        self.assertTrue(status["connected"])

    def test_reports_reference_then_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            service._log_path.write_text(
                "Référence K-Move pendant 2.0 s\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "reference")
            service._log_path.write_text(
                "K-Move prêt; liaison Bluetooth conservée.\n",
                encoding="utf-8",
            )
            self.assertEqual(service.status()["phase"], "ready")

    def test_latest_maps_axes_and_reports_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-22T08:00:00+00:00"
            service._recording = True
            service._duration = 1_000_000
            service._csv_path = Path(directory) / "test.csv"

            latest = service.latest()

        measurement = latest["measurement"]
        self.assertEqual(measurement["rotation_deg"], 65.0)
        self.assertEqual(measurement["flexion_extension_deg"], -40.0)
        self.assertEqual(measurement["inclination_deg"], 15.0)
        self.assertEqual(measurement["battery_pct"], 59)
        self.assertEqual(measurement["ranges"]["rotation"]["min"], -20.0)
        self.assertEqual(measurement["ranges"]["rotation"]["max"], 65.0)

    def test_stop_keeps_kmove_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-22T08:00:00+00:00"
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
