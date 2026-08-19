import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ble.anr.acquisition_service import ANRM40AcquisitionService


FIELDS = ["timestamp_utc", "elapsed_seconds", "emg_raw"]


def iso(delta_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


class ANRM40ApiTest(unittest.TestCase):
    def make_service(self, directory):
        service = ANRM40AcquisitionService()
        root = Path(directory)
        service._live_path = root / "live.csv"
        service._log_path = root / "worker.log"
        service._csv_path = root / "test.csv"
        return service

    def attach_running_process(self, service):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        service._process = process
        return process

    def write_rows(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(FIELDS)
            writer.writerows(rows)

    def test_start_requests_a_fresh_anr_session(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            process = mock.Mock(pid=123)
            process.poll.return_value = None
            with mock.patch(
                "ble.anr.acquisition_service.manager_state",
                return_value={"target": "anr_m40"},
            ), mock.patch(
                "ble.anr.acquisition_service.request_sensor",
                side_effect=[
                    {"generation": "stop-generation"},
                    {"generation": "start-generation"},
                ],
            ) as request_sensor, mock.patch(
                "ble.anr.acquisition_service.ManagedSensorProcess",
                return_value=process,
            ), mock.patch("ble.anr.acquisition_service.time.sleep"):
                status = service.start(filename="simple.csv")

        self.assertEqual(
            [call.args[0] for call in request_sensor.call_args_list],
            [None, "anr_m40"],
        )
        self.assertTrue(status["connected"])
        self.assertEqual(status["phase"], "connecting")
        self.assertTrue(status["csv_path"].endswith("simple.csv"))

    def test_latest_reports_only_rows_from_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            self.attach_running_process(service)
            service._started_at = iso(-1)
            service._recording = True
            self.write_rows(
                service._live_path,
                [
                    ["2000-01-01T00:00:00+00:00", 1.0, 99],
                    [iso(), 2.0, 42],
                ],
            )

            latest = service.latest()

        self.assertEqual(latest["phase"], "active")
        self.assertEqual(latest["measurement"]["emg_raw"], 42)
        self.assertEqual(latest["measurement"]["max_emg_raw"], 42)

    def test_latest_ignores_partial_live_csv_line(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            self.attach_running_process(service)
            timestamp = iso()
            service._started_at = iso(-1)
            service._recording = True
            service._live_path.write_text(
                (
                    "timestamp_utc,elapsed_seconds,emg_raw\n"
                    f"{timestamp},1.0,18\n"
                    f"{timestamp},"
                ),
                encoding="utf-8",
            )

            latest = service.latest()

        self.assertEqual(latest["measurement"]["emg_raw"], 18)

    def test_stop_writes_csv_and_releases_the_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            self.attach_running_process(service)
            service._started_at = iso(-1)
            service._recording = True
            service._initialize_recording_csv()
            self.write_rows(
                service._live_path,
                [
                    [iso(), 1.0, 18],
                    [iso(), 2.0, 42],
                ],
            )

            with mock.patch(
                "ble.anr.acquisition_service.request_sensor",
                return_value={"generation": "stop-generation"},
            ) as request_sensor:
                status = service.stop()

            with service._csv_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        request_sensor.assert_called_once_with(None)
        self.assertFalse(status["running"])
        self.assertFalse(status["connected"])
        self.assertEqual([row["emg_raw"] for row in rows], ["18", "42"])

    def test_latest_marks_a_stale_recording_stream_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            self.attach_running_process(service)
            service._started_at = "1999-01-01T00:00:00+00:00"
            service._recording = True
            self.write_rows(
                service._live_path,
                [["2000-01-01T00:00:00+00:00", 1.0, 18]],
            )

            latest = service.latest()

        self.assertEqual(latest["phase"], "error")
        self.assertFalse(latest["running"])
        self.assertIsNone(latest["measurement"])
        self.assertIn("Flux ANR M40 interrompu", latest["last_error"])

    def test_status_reports_battery_from_log(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            service._log_path.write_text(
                "Batterie M40: 30 %\nANR M40 prêt.\n",
                encoding="utf-8",
            )

            status = service.status()

        self.assertEqual(status["phase"], "ready")
        self.assertEqual(status["battery_pct"], 30)


if __name__ == "__main__":
    unittest.main()
