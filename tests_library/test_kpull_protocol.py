import unittest

from ble.kinvent.kpull.protocol import (
    calibrate_sample,
    compute_counts_per_kg,
    parse_raw_frame,
)


class KPullProtocolTest(unittest.TestCase):
    def test_decodes_frame_from_official_capture(self):
        frame = bytes.fromhex(
            "ff ff fe 2c fb 00 9e 39 00 0f 34 00 0f 34 00 0f 35"
        )

        sample = parse_raw_frame(frame)

        self.assertEqual(sample["t"], 11515)
        self.assertEqual(sample["raw_force"], 40505)
        self.assertEqual(sample["raw_aux_1"], 3892)

    def test_calculates_force_only_with_known_coefficient(self):
        raw = {
            "t": 1,
            "raw_force": 50_000,
            "raw_aux_1": 0,
            "raw_aux_2": 0,
            "raw_aux_3": 0,
        }

        unscaled = calibrate_sample(raw, tare_offset=40_000)
        scaled = calibrate_sample(
            raw,
            tare_offset=40_000,
            counts_per_kg=1_000,
        )

        self.assertIsNone(unscaled["force_kg"])
        self.assertEqual(scaled["force_kg"], 10.0)
        self.assertAlmostEqual(scaled["force_n"], 98.1)

    def test_computes_calibration_from_known_load(self):
        coefficient = compute_counts_per_kg(
            tare_offset=40_000,
            loaded_raw_force=130_000,
            known_load_kg=30,
        )

        self.assertEqual(coefficient, 3_000)


if __name__ == "__main__":
    unittest.main()
