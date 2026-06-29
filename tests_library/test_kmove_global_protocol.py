from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
KMOVE_JS = ROOT / "frontend" / "static" / "kmove.js"



class KMoveGlobalProtocolTest(unittest.TestCase):
    def test_shoulder_right_global_protocol_matches_measured_watch_placement(self):
        source = KMOVE_JS.read_text(encoding="utf-8")
        shoulder_start = source.index('"Épaule": {')
        hip_start = source.index('"Hanche": {')
        shoulder_block = source[shoulder_start:hip_start]

        self.assertIn(
            '{ label: "Flexion", axis: "inclination", side: "negative" }',
            shoulder_block,
        )
        self.assertIn(
            '{ label: "Extension", axis: "inclination", side: "positive" }',
            shoulder_block,
        )
        self.assertIn(
            '{ label: "Abduction", axis: "rotation", side: "negative" }',
            shoulder_block,
        )
        self.assertIn(
            '{ label: "Adduction", axis: "flexion_extension", side: "positive" }',
            shoulder_block,
        )
        self.assertIn(
            '{ label: "Rotation externe", axis: "rotation", side: "negative" }',
            shoulder_block,
        )
        self.assertIn(
            '{ label: "Rotation interne", axis: "rotation", side: "positive" }',
            shoulder_block,
        )


if __name__ == "__main__":
    unittest.main()
