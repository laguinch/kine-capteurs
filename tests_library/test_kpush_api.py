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
        service._log_path.write_text(
            "Tare K-Push terminée: offset=30433.\n",
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

    def test_connect_uses_privileged_switching_helper(self):
        service = KPushAcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._live_path = root / "live.csv"
            service._log_path = root / "worker.log"
            with mock.patch(
                "ble.kinvent.kpush.acquisition_service.subprocess.Popen",
                return_value=process,
            ) as popen:
                status = service.connect()

        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["sudo", "-n"])
        self.assertTrue(command[2].endswith("run_kpush_session.sh"))
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
                "Tare K-Push terminée: offset=30433.\n",
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

    def test_stop_keeps_kpush_connected_and_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_ready_service(directory)
            self.write_live_rows(service._live_path)
            service._started_at = "2026-06-19T15:00:00+00:00"
            service._recording = True
            service._csv_path = Path(directory) / "test.csv"

            status = service.stop()

        self.assertFalse(status["running"])
        self.assertTrue(status["connected"])
        self.assertEqual(status["phase"], "ready")


if __name__ == "__main__":
    unittest.main()
