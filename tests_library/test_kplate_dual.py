import importlib.util
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


if __name__ == "__main__":
    unittest.main()
