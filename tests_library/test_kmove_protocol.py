import math
import unittest
from scripts.kinvent_kmove_hci import INIT_COMMANDS, KMoveHciClient

from ble.kinvent.kmove.protocol import (
    parse_quaternion_frame,
    quaternion_to_euler_degrees,
    relative_quaternion,
)


class KMoveProtocolTest(unittest.TestCase):
    def test_initialization_matches_official_capture(self):
        self.assertEqual(
            [command for command, _ in INIT_COMMANDS],
            [
                b"\x10",
                b"\x09",
                b"\x76",
                b"\x11",
                b"\x10",
                b"\x10",
                bytes.fromhex("ac 00 54 f8"),
                b"\xb6",
                b"\xb0",
            ],
        )

    def test_quaternion_mode_waits_for_explicit_test_start(self):
        self.assertEqual([command for command, _ in INIT_COMMANDS[-3:]], [
            bytes.fromhex("ac 00 54 f8"),
            b"\xb6",
            b"\xb0",
        ])

    def test_reference_starts_with_official_stream_command(self):
        client = KMoveHciClient(
            adapter=0,
            address="60:8A:10:4F:BD:12",
        )
        sent = []
        client.send_write_command = sent.append
        client.pump = lambda duration: None

        client.prepare_session()

        self.assertEqual(sent, [b"\x11"])

    def test_reads_kmove_name_from_advertising_data(self):
        advertising = bytes.fromhex(
            "02 01 06 10 09 4b 46 4f 52 43 45 53 65 6e 73 30 32 31 34 33"
        )

        self.assertEqual(
            KMoveHciClient._advertised_name(advertising),
            "KFORCESens02143",
        )

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
