import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ble.anr.acquisition_service import ANRM40AcquisitionService


FIELDS = ["timestamp_utc", "elapsed_seconds", "emg_raw"]


class ANRM40ApiTest(unittest.TestCase):
    def make_ready_service(self, directory):
        service = ANRM40AcquisitionService()
        root = Path(directory)
        service._live_path = root / "live.csv"
        service._log_path = root / "worker.log"
        service._control_path = root / "control.json"
        service._log_path.write_text(
            "Batterie M40: 30 %\nANR M40 prêt; liaison Bluetooth conservée.\n",
            encoding="utf-8",
        )
        service._process = mock.Mock(pid=123)
        service._process.poll.return_value = None
        return service

    def write_live_rows(self, path):
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(FIELDS)
            writer.writerow(["2026-08-19T15:00:01+00:00", 1.0, 18])
            writer.writerow(["2026-08-19T15:00:02+00:00", 2.0, 42])

    def test_connect_requests_anr_m40_from_unique_manager(self):
        service = ANRM40AcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._live_path = root / "live.csv"
            service._log_path = root / "worker.log"
            service._control_path = root / "control.json"
            with mock.patch(
                "ble.anr.acquisition_service.request_sensor"
            ) as request, mock.patch(
                "ble.anr.acquisition_service.ManagedSensorProcess",
                return_value=process,
            ):
                status = service.connect()

        request.assert_called_once_with("anr_m40")
        self.assertTrue(status["connected"])
        self.assertFalse(status["running"])

    def test_latest_reports_emg_and_battery(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-08-19T15:00:00+00:00"
            service._recording = True
            service._duration = 1_000_000_000
            service._csv_path = Path(directory) / "test.csv"

            latest = service.latest()

        self.assertEqual(latest["phase"], "active")
        self.assertEqual(latest["measurement"]["emg_raw"], 42)
        self.assertEqual(latest["measurement"]["max_emg_raw"], 42)
        self.assertEqual(latest["measurement"]["battery_pct"], 30)

    def test_status_reports_battery_before_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)

            status = service.status()

        self.assertEqual(status["phase"], "ready")
        self.assertEqual(status["battery_pct"], 30)

    def test_latest_appends_recording_rows_once(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            service._live_path.write_text(
                "timestamp_utc,elapsed_seconds,emg_raw\n",
                encoding="utf-8",
            )
            service._started_at = "2026-08-19T15:00:00+00:00"
            service._recording = True
            service._duration = 1_000_000_000
            service._csv_path = Path(directory) / "test.csv"
            service._initialize_recording_csv()
            service._live_path.write_text(
                (
                    "timestamp_utc,elapsed_seconds,emg_raw\n"
                    "2026-08-19T15:00:01+00:00,1.0,18\n"
                    "2026-08-19T15:00:02+00:00,2.0,42\n"
                ),
                encoding="utf-8",
            )
            service._recording_live_position = len(
                "timestamp_utc,elapsed_seconds,emg_raw\n"
            )

            service.latest()
            service.latest()

            with service._csv_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        self.assertEqual([row["emg_raw"] for row in rows], ["18", "42"])

    def test_stop_keeps_anr_connected_and_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-08-19T15:00:00+00:00"
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
