import csv
import tempfile
import unittest
from pathlib import Path

from ble.kinvent.kplates.cmj_analysis import analyze_cmj_csv


class CMJAnalysisTest(unittest.TestCase):
    def test_detects_flight_and_jump_height_from_all_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmj.csv"
            with path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.DictWriter(
                    target,
                    fieldnames=[
                        "elapsed_s",
                        "source",
                        "source_kg",
                        "total_kg",
                        "left_kg",
                        "right_kg",
                    ],
                )
                writer.writeheader()
                for index in range(226):
                    t = index / 75
                    total = 80.0
                    if 1.2 <= t < 1.45:
                        total = 55.0
                    elif 1.45 <= t < 1.75:
                        total = 145.0
                    elif 1.75 <= t < 2.15:
                        total = 0.5
                    elif 2.15 <= t < 2.35:
                        total = 180.0
                    for side in ("gauche", "droite"):
                        writer.writerow(
                            {
                                "elapsed_s": t + (
                                    0.003 if side == "droite" else 0
                                ),
                                "source": side,
                                "source_kg": total / 2,
                                "total_kg": total,
                                "left_kg": total / 2,
                                "right_kg": total / 2,
                            }
                        )

            result = analyze_cmj_csv(path)

        self.assertAlmostEqual(result["body_mass_kg"], 80.0)
        self.assertAlmostEqual(result["flight_time_s"], 0.383, places=3)
        self.assertAlmostEqual(result["jump_height_cm"], 18.0, places=1)
        self.assertEqual(result["peak_force_kg"], 145.0)
        self.assertGreater(result["left_source_rate_hz"], 74)
        self.assertEqual(result["resampled_rate_hz"], 100)
        self.assertEqual(result["raw_event_count"], 452)
