import importlib.util
import time
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kinvent_dual_hci.py"


class KPlateDualTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kinvent_dual_hci", SCRIPT_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_combines_left_and_right_load(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        left, right = client.plates
        left.latest = {"force_kg": 60.0, "t": 1}
        right.latest = {"force_kg": 40.0, "t": 2}
        left.distribution = {"cop_x": 0.0, "cop_y": 0.2}
        right.distribution = {"cop_x": 0.0, "cop_y": -0.1}

        values = client.combined_values()

        self.assertEqual(values["total_kg"], 100.0)
        self.assertEqual(values["left_pct"], 60.0)
        self.assertEqual(values["right_pct"], 40.0)
        self.assertEqual(values["asymmetry"], -20.0)
        self.assertAlmostEqual(values["global_x"], -0.2)
        self.assertAlmostEqual(values["global_y"], 0.08)

    def test_ignores_small_negative_zero_drift(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        left, right = client.plates
        left.latest = {"force_kg": -0.05, "t": 1}
        right.latest = {"force_kg": 0.03, "t": 2}

        values = client.combined_values()

        self.assertEqual(values["left_kg"], 0.0)
        self.assertEqual(values["right_kg"], 0.03)
        self.assertIsNone(values["left_pct"])
        self.assertIsNone(values["global_x"])

    def test_pairs_each_sample_once_within_tolerance(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
            sync_tolerance_ms=20,
        )
        left, right = client.plates
        base = time.monotonic()
        sample = {"force_kg": 50.0, "t": 1}
        left.samples.append(
            {
                "received_monotonic": base,
                "received_utc": "2026-06-18T00:00:00+00:00",
                "sample": sample,
                "distribution": None,
            }
        )
        right.samples.append(
            {
                "received_monotonic": base + 0.012,
                "received_utc": "2026-06-18T00:00:00.012000+00:00",
                "sample": sample,
                "distribution": None,
            }
        )
        pairs = []
        client.write_combined = lambda left_entry, right_entry, delta: pairs.append(
            delta
        )

        client.pair_samples()
        client.pair_samples()

        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(pairs[0], 12.0, places=3)
        self.assertEqual(client.paired_samples, 1)
        self.assertFalse(left.samples)
        self.assertFalse(right.samples)

    def test_discards_sample_outside_tolerance(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
            sync_tolerance_ms=20,
        )
        left, right = client.plates
        base = time.monotonic()
        entry = {
            "received_utc": "2026-06-18T00:00:00+00:00",
            "sample": {"force_kg": 0.0, "t": 1},
            "distribution": None,
        }
        left.samples.append({**entry, "received_monotonic": base})
        right.samples.append({**entry, "received_monotonic": base + 0.1})

        client.pair_samples()

        self.assertEqual(client.dropped_samples["gauche"], 1)
        self.assertFalse(left.samples)
        self.assertTrue(right.samples)


if __name__ == "__main__":
    unittest.main()
