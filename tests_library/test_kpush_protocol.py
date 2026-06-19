import unittest
from unittest import mock

from ble.kinvent.kpush.protocol import calibrate_sample, parse_raw_frame
from scripts.kinvent_kpush_hci import KPushHciClient


class KPushProtocolTest(unittest.TestCase):
    def test_decodes_force_frame_from_official_capture(self):
        frame = bytes.fromhex(
            "ff ff fe 7b de 00 72 e7 00 0f 51 00 0f 51 00 0f 52"
        )

        sample = parse_raw_frame(frame)

        self.assertEqual(sample["t"], 0x7BDE)
        self.assertEqual(sample["raw_force"], 0x72E7)
        self.assertEqual(sample["raw_aux_1"], 0x0F51)

    def test_ignores_short_diagnostic_frame(self):
        frame = bytes.fromhex("ff ff fe fc d1 ff 3b 80 80 06 e4")

        self.assertIsNone(parse_raw_frame(frame))

    def test_applies_dynamic_tare_and_converts_to_newtons(self):
        raw = {
            "t": 1,
            "raw_force": 38_600,
            "raw_aux_1": 0,
            "raw_aux_2": 0,
            "raw_aux_3": 0,
        }

        sample = calibrate_sample(raw, tare_offset=28_600)

        self.assertEqual(sample["force_counts"], 10_000)
        self.assertEqual(sample["force_kg"], 1.0)
        self.assertAlmostEqual(sample["force_n"], 9.81)

    def test_sends_official_keepalive_during_long_session(self):
        client = KPushHciClient(
            adapter=0,
            address="60:8A:10:30:9B:FA",
            csv_path=None,
        )
        sent = []
        client.receive_packet = lambda: None
        client.send_write_command = lambda value: sent.append(value)
        client.keepalive_interval = 0.001
        client.next_keepalive_at = 0

        with mock.patch.object(
            client,
            "process_packet",
        ):
            client.pump(0.005)

        self.assertIn(b"\xff", sent)


if __name__ == "__main__":
    unittest.main()
