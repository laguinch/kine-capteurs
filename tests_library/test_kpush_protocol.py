import unittest
from unittest import mock

from ble.kinvent.kpush.protocol import calibrate_sample, parse_raw_frame
from scripts.kinvent_kpush_bumble import KPushBumbleClient
from scripts.kinvent_kpush_hci import KPushHciClient


class KPushProtocolTest(unittest.TestCase):
    def test_official_test_transitions_keep_connection_alive(self):
        client = KPushHciClient(
            adapter=0,
            address="60:8A:10:30:9B:FA",
            csv_path=None,
        )
        commands = []
        client.send_write_command = commands.append
        client.pump = mock.Mock()

        client.stop_test_stream(commands=1)
        client.start_test_stream()
        client.stop_test_stream(commands=3)

        self.assertEqual(
            commands,
            [b"\x10", b"\x11", b"\x10", b"\x10", b"\x10"],
        )

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

    def test_bumble_client_decodes_and_tares_like_hci_client(self):
        client = KPushBumbleClient(
            transport="usb:0",
            address="60:8A:10:30:9B:FA",
            address_type="public",
            csv_path=None,
            tare_duration=0,
            print_interval=999,
        )
        client.handle_payload(
            bytes.fromhex(
                "ff ff fe 7b de 00 72 e7 00 0f 51 00 0f 51 00 0f 52"
            )
        )

        self.assertIsNotNone(client.tare_offset)
        self.assertIsNotNone(client.latest)
        self.assertEqual(client.latest["raw_force"], 0x72E7)

    def test_bumble_client_ignores_non_measurement_payload(self):
        client = KPushBumbleClient(
            transport="usb:0",
            address="60:8A:10:30:9B:FA",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )

        client.handle_payload(b"KINVENT FW 2.64S")

        self.assertIsNone(client.latest)


if __name__ == "__main__":
    unittest.main()
