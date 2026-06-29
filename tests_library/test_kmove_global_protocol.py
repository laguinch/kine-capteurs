from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
KMOVE_JS = ROOT / "frontend" / "static" / "kmove.js"



class KMoveGlobalProtocolTest(unittest.TestCase):
    def setUp(self):
        self.source = KMOVE_JS.read_text(encoding="utf-8")

    def test_shoulder_right_global_protocol_matches_measured_watch_placement(self):
        shoulder_start = self.source.index('"Épaule": {')
        hip_start = self.source.index('"Hanche": {')
        shoulder_block = self.source[shoulder_start:hip_start]

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

    def test_guided_repetition_uses_delta_from_repetition_baseline(self):
        self.assertIn("function angularDelta(current, baseline)", self.source)
        self.assertIn(
            "state.guided.repRange = { baseline: current, current: 0, min: 0, max: 0 };",
            self.source,
        )
        self.assertIn(
            "const delta = angularDelta(current, Number(state.guided.repRange.baseline) || 0);",
            self.source,
        )
        self.assertIn("state.guided.repRange.current = delta;", self.source)
        self.assertIn(
            "amplitudeFromDelta(Number(state.guided.repRange.current) || 0, card.side)",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
