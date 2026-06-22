import math
import unittest

from ble.kinvent.kmove.protocol import (
    parse_quaternion_frame,
    quaternion_to_euler_degrees,
    relative_quaternion,
)


class KMoveProtocolTest(unittest.TestCase):
    def test_decodes_official_quaternion_frame(self):
        frame = bytes.fromhex(
            "ff ff fe 4b f0 98 e0 6f 51 b8 8f 7f f0 "
            "82 9b 80 cf 7e f0 3c"
        )

        sample = parse_quaternion_frame(frame)

        self.assertEqual(sample["t"], 0x4BF0)
        self.assertAlmostEqual(
            sum(value * value for value in sample["quaternion"]),
            1.0,
            places=5,
        )
        self.assertEqual(sample["battery_pct"], 60)
        self.assertEqual(sample["accel_x_raw"], 667)

    def test_rejects_uninitialized_zero_quaternion(self):
        frame = bytes.fromhex(
            "ff ff fe 00 01 80 00 80 00 80 00 80 00 "
            "80 00 80 00 80 00 3c"
        )
        self.assertIsNone(parse_quaternion_frame(frame))

    def test_converts_quaternion_to_three_degree_axes(self):
        half = math.radians(45) / 2
        quaternion = (math.cos(half), math.sin(half), 0.0, 0.0)

        angles = quaternion_to_euler_degrees(quaternion)

        self.assertAlmostEqual(angles["rotation_x_deg"], 45.0)
        self.assertAlmostEqual(angles["rotation_y_deg"], 0.0)
        self.assertAlmostEqual(angles["rotation_z_deg"], 0.0)

    def test_reference_orientation_becomes_zero(self):
        reference = (0.5, 0.5, 0.5, 0.5)
        relative = relative_quaternion(reference, reference)
        angles = quaternion_to_euler_degrees(relative)

        self.assertAlmostEqual(angles["rotation_x_deg"], 0.0)
        self.assertAlmostEqual(angles["rotation_y_deg"], 0.0)
        self.assertAlmostEqual(angles["rotation_z_deg"], 0.0)


if __name__ == "__main__":
    unittest.main()
