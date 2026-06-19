import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ble.kinvent.kpush.acquisition_service import KPushAcquisitionService


class KPushApiTest(unittest.TestCase):
    def test_latest_reports_current_and_maximum_force(self):
        service = KPushAcquisitionService()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kpush.csv"
            with path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target)
                writer.writerow(
                    [
                        "timestamp_utc",
                        "sensor_time",
                        "raw_force",
                        "tare_offset",
                        "force_counts",
                        "force_kg",
                        "force_n",
                    ]
                )
                writer.writerow(["t1", 1, 1, 0, 1, 10, 98.1])
                writer.writerow(["t2", 2, 2, 0, 2, 20, 196.2])
            service._csv_path = path

            latest = service.latest()

        self.assertEqual(latest["measurement"]["force_n"], 196.2)
        self.assertEqual(latest["measurement"]["max_force_n"], 196.2)
        self.assertAlmostEqual(latest["measurement"]["max_force_kg"], 20.0)

    def test_start_uses_privileged_switching_helper(self):
        service = KPushAcquisitionService()
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service._log_path = root / "worker.log"
            with mock.patch(
                "ble.kinvent.kpush.acquisition_service.subprocess.Popen",
                return_value=process,
            ) as popen:
                status = service.start(duration=15, filename="test.csv")

        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["sudo", "-n"])
        self.assertTrue(command[2].endswith("run_kpush_session.sh"))
        self.assertTrue(status["running"])


if __name__ == "__main__":
    unittest.main()
