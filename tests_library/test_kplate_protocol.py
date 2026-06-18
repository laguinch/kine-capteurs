import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

from ble.kinvent.kplates.protocol import compute_distribution, parse_frame


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kinvent_raw_hci.py"


class KPlateProtocolTest(unittest.TestCase):
    def test_rejects_non_measurement_frame(self):
        self.assertIsNone(parse_frame(bytes.fromhex("4b 49 4e 56 45 4e 54")))

    def test_decodes_observed_unloaded_frame(self):
        frame = bytes.fromhex(
            "ff ff fe 3d d0 00 96 ad 00 80 1b 00 8c c7 00 89 1a"
        )

        sample = parse_frame(frame)

        self.assertEqual(sample["t"], 0x3DD0)
        self.assertEqual(sample["raw_av_g"], 0x96AD)
        self.assertEqual(sample["raw_ar_g"], 0x801B)
        self.assertEqual(sample["raw_ar_d"], 0x8CC7)
        self.assertEqual(sample["raw_av_d"], 0x891A)
        self.assertAlmostEqual(sample["force_kg"], 2955 / 10360, places=6)

    def test_distribution_uses_zero_corrected_cells(self):
        values = [35950 + 20000, 33500 + 10000, 34050 + 10000, 36050 + 20000]
        frame = (
            b"\xff\xff\xfe\x05\x55"
            + b"".join(value.to_bytes(3, "big", signed=True) for value in values)
        )
        sample = parse_frame(frame)
        distribution = compute_distribution(sample)

        self.assertIsNotNone(distribution)
        self.assertAlmostEqual(distribution["av_d_pct"], 100 / 3, places=6)
        self.assertAlmostEqual(distribution["av_g_pct"], 100 / 3, places=6)
        self.assertAlmostEqual(distribution["ar_g_pct"], 100 / 6, places=6)
        self.assertAlmostEqual(distribution["ar_d_pct"], 100 / 6, places=6)
        self.assertAlmostEqual(
            sum(
                distribution[key]
                for key in ("av_d_pct", "av_g_pct", "ar_g_pct", "ar_d_pct")
            ),
            100.0,
            places=6,
        )

    def test_raw_hci_csv_has_stable_decoded_columns(self):
        spec = importlib.util.spec_from_file_location("kinvent_raw_hci", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            client = module.RawKinventClient(
                1,
                "E8:EB:1B:6F:A7:5F",
                "public",
                path,
            )
            frame = bytes.fromhex(
                "ff ff fe 3d d0 00 96 ad 00 80 1b 00 8c c7 00 89 1a"
            )
            client.handle_att(
                bytes([module.ATT_OP_NOTIFICATION])
                + module.struct.pack("<H", module.UART_VALUE_HANDLE)
                + frame
            )
            client.handle_att(
                bytes([module.ATT_OP_NOTIFICATION])
                + module.struct.pack("<H", module.UART_VALUE_HANDLE)
                + b"KINVENT FW 2.34V"
            )
            client.close()

            with path.open(newline="", encoding="utf-8") as csv_file:
                rows = list(csv.reader(csv_file))

        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), 22)
        self.assertEqual(len(rows[1]), 22)
        self.assertEqual(len(rows[2]), 22)
        self.assertEqual(rows[1][4], str(0x3DD0))
        self.assertEqual(rows[2][4], "")


if __name__ == "__main__":
    unittest.main()
