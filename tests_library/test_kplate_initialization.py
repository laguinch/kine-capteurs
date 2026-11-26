"""Replay hors ligne des réponses de la capture double plate du 15 juin."""

import heapq
import json
import struct
import unittest
from pathlib import Path
from unittest import mock

from ble.kinvent.kplates.initialization import (
    INITIALIZATION_STEPS,
    PlateInitialization,
    PlateInitializationError,
    RESPONSE_TIMEOUT,
)
from scripts import kinvent_dual_hci as driver


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/kplates_init_234.json").read_text()
)


class Replay:
    def __init__(self, alter_response=None):
        self.now = 0.0
        self.events = []
        self.writes = []
        self.indices = {side: 0 for side in FIXTURE["plates"]}
        self.alter_response = alter_response
        self.client = driver.DualKinventClient(
            1, "E8:EB:1B:6F:A7:5F", "E8:EB:1B:79:B1:AB", None, 0, 1
        )
        self.client.sock = mock.Mock()
        self.client.sock.gettimeout.return_value = 0.2
        for handle, plate in enumerate(self.client.plates, 10):
            plate.handle = handle
            self.client.by_handle[handle] = plate
        self.client.send_write_command = self.send
        self.client.send_att = mock.Mock()
        self.client.wait_write_response = mock.Mock()
        self.client.update_connection_interval = mock.Mock()
        self.client.pump = self.pump
        self.client.receive = self.receive

    def pump(self, duration):
        self.now += duration

    def send(self, plate, command):
        index = self.indices[plate.side]
        row = FIXTURE["plates"][plate.side][index]
        if command != bytes.fromhex(row["command"]):
            raise AssertionError(f"Commande hors capture : {plate.side} {command.hex()}")
        self.indices[plate.side] += 1
        self.writes.append((self.now, plate.side, command))
        for notification in row.get("notifications", []):
            heapq.heappush(self.events, (self.now + notification["delay"], plate.handle,
                                         bytes.fromhex(notification["value"])))
        if "response" not in row:
            return
        response = bytes.fromhex(row["response"])
        delay = row["response_delay"]
        if self.alter_response:
            response, delay = self.alter_response(plate.side, command, response, delay)
        if response is not None:
            heapq.heappush(self.events, (self.now + delay, plate.handle, response))

    def receive(self):
        deadline = min(
            s.deadline for s in self.client._plate_initializations.values()
            if not s.done
        )
        if self.events and self.events[0][0] <= deadline:
            self.now, handle, response = heapq.heappop(self.events)
            att = b"\x1b\x52\x00" + response
            l2cap = struct.pack("<HH", len(att), driver.ATT_CID) + att
            return driver.HCI_ACLDATA_PKT, struct.pack("<HH", handle, len(l2cap)) + l2cap
        self.now = deadline
        return None

    def run(self):
        with mock.patch.object(driver.time, "monotonic", side_effect=lambda: self.now):
            self.client.start_streams(self.client.connection_order())


class KPlateInitializationTest(unittest.TestCase):
    def test_replays_captured_commands_and_uart_responses_for_both_plates(self):
        replay = Replay()
        replay.run()
        for side, rows in FIXTURE["plates"].items():
            self.assertEqual(
                [v for _, s, v in replay.writes if s == side],
                [bytes.fromhex(row["command"]) for row in rows],
            )
        self.assertEqual(replay.client.send_att.call_count, 2)
        for call in replay.client.send_att.call_args_list:
            self.assertEqual(call.args[1], bytes.fromhex("12 53 00 01 00"))
        self.assertEqual(replay.client.update_connection_interval.call_count, 2)
        self.assertFalse(replay.client._plate_initializations)
        replay.client.sock.settimeout.assert_called_with(0.2)

    def test_slow_plate_does_not_hold_back_other_plate(self):
        def alter(side, command, response, delay):
            if command == b"\x66":
                delay = 1.9 if side == "droite" else 0.2
            return response, delay

        replay = Replay(alter)
        replay.run()
        times = {(s, c): t for t, s, c in replay.writes}
        self.assertAlmostEqual(times["gauche", b"\x56"] - times["gauche", b"\x66"], 0.2)
        self.assertAlmostEqual(times["droite", b"\x56"] - times["droite", b"\x66"], 1.9)
        self.assertLess(times["gauche", b"\x56"], times["droite", b"\x56"])

    def test_missing_response_stops_without_sending_next_command(self):
        def alter(side, command, response, delay):
            if side == "droite" and command == b"\x66":
                response = None
            return response, delay

        replay = Replay(alter)
        with self.assertRaisesRegex(PlateInitializationError, "droite.*66"):
            replay.run()
        right = [c for _, s, c in replay.writes if s == "droite"]
        self.assertEqual(right[-1], b"\x66")
        self.assertEqual(right.count(b"\x66"), 1)
        self.assertFalse(replay.client._plate_initializations)
        replay.client.sock.settimeout.assert_called_with(0.2)

    def test_unconfirmed_firmware_does_not_receive_v234_commands(self):
        replay = Replay(lambda s, c, r, d: (b"KINVENT FW 3.00V" if c == b"\x09" else r, d))
        with self.assertRaises(PlateInitializationError):
            replay.run()
        self.assertFalse(any(c == b"\x76" for _, _, c in replay.writes))

    def test_eeprom_reply_must_match_address_and_value(self):
        for invalid in (bytes.fromhex("ac 00 54 aa 52"), bytes.fromhex("ac 01 04 00 99")):
            with self.subTest(reply=invalid.hex()):
                replay = Replay(lambda s, c, r, d: (invalid if c == bytes.fromhex("ac0104a9") else r, d))
                with self.assertRaises(PlateInitializationError):
                    replay.run()
                for side in FIXTURE["plates"]:
                    commands = [c for _, s, c in replay.writes if s == side]
                    self.assertEqual(commands[-1], bytes.fromhex("ac0104a9"))

    def test_fragmented_reply_and_previous_reply_cannot_complete_next_step(self):
        state = PlateInitialization("gauche")
        state.index = next(i for i, s in enumerate(INITIALIZATION_STEPS) if s.command == b"\x66")
        state.deadline = RESPONSE_TIMEOUT
        sent = []
        state.feed(b"F=2")
        state.advance(0.1, sent.append)
        self.assertEqual(sent, [])
        state.feed(b"5Hz\x00\x00")
        state.advance(0.2, sent.append)
        self.assertEqual(sent, [b"\x56"])
        state.advance(0.3, sent.append)
        self.assertEqual(sent, [b"\x56"])
        state.feed(b"F=75Hz\x00\x00")
        state.advance(0.4, sent.append)
        self.assertEqual(sent, [b"\x56"])
        state.feed(b"F=25Hz\x00\x00")
        state.advance(0.5, sent.append)
        self.assertEqual(sent[-1], bytes.fromhex("ac0054f8"))

    def test_other_handle_or_characteristic_cannot_acknowledge_instruction(self):
        replay = Replay()
        plate = replay.client.plates[0]
        state = PlateInitialization(plate.side)
        state.advance(0, lambda c: None)
        replay.client._plate_initializations[plate.handle] = state
        response = b"KINVENT FW 2.34V"
        replay.client.handle_att(99, b"\x1b\x52\x00" + response)
        replay.client.handle_att(plate.handle, b"\x1b\x57\x00" + response)
        self.assertFalse(state.acknowledged)
        replay.client.handle_att(plate.handle, b"\x1b\x52\x00" + response)
        self.assertTrue(state.acknowledged)

    def test_battery_start_waits_200ms_after_fragmented_format_reply(self):
        state = PlateInitialization("gauche")
        sent = []
        state.advance(0, sent.append)
        state.feed(b"KINVENT FW 2.34V", 0.1)
        state.advance(0.1, sent.append)
        state.feed(b"F=75", 0.4)
        state.advance(0.5, sent.append)
        self.assertEqual(sent[-1], b"\x76")
        state.feed(b"Hz\x00\x00", 0.6)
        state.advance(0.79, sent.append)
        self.assertEqual(sent[-1], b"\x76")
        state.advance(0.81, sent.append)
        self.assertEqual(sent[-1], b"\x11")
        self.assertAlmostEqual(state.deadline, 2.1)

    def test_battery_accepts_current_frequency_25_or_75(self):
        for frequency in (25, 75):
            state = PlateInitialization("gauche")
            state.index = 1
            state.deadline = 2
            state.feed(f"F={frequency}Hz".encode(), 0.2)
            self.assertTrue(state.acknowledged)
            self.assertAlmostEqual(state.ready_at, 0.4)

    def test_battery_requires_five_complete_samples_not_five_notifications(self):
        state = PlateInitialization("gauche")
        state.index = 2
        state.deadline = 2
        frame = bytes.fromhex("ff ff fe 00 00 00 00 00 00 00 00")
        sent = []
        for i in range(5):
            state.feed(frame[:4], 0.1 + i * 0.02)
            state.feed(frame[4:], 0.11 + i * 0.02)
        state.advance(0.3, sent.append)
        self.assertEqual(sent, [])
        self.assertEqual(state.sample_count, 4)
        state.feed(frame[:3], 0.31)
        state.advance(0.31, sent.append)
        self.assertEqual(sent, [b"\x10"])

    def test_battery_timeout_reports_received_data_even_when_parser_discards_it(self):
        for payload, count in ((b"", 0), (b"\x01" * 120, 0),
                               (bytes.fromhex("ff ff fe 00 00 00 00 00 00 00 00") * 3, 2)):
            with self.subTest(payload=payload):
                state = PlateInitialization("droite")
                state.index = 2
                state.deadline = 2
                state.feed(payload, 0.1)
                with self.assertRaises(PlateInitializationError) as caught:
                    state.advance(2, lambda command: self.fail("Unexpected command"))
                message = str(caught.exception)
                self.assertIn(f"Octets reçus à cette étape: {len(payload)}", message)
                self.assertIn(f"échantillons batterie complets: {count}/5", message)
                self.assertEqual("aucun octet" in message, not payload)
                self.assertLessEqual(len(state.received_tail), 100)

    def test_continuing_battery_stream_prevents_frequency_change(self):
        state = PlateInitialization("gauche")
        state.index = 4
        state.last_received = 0
        state.deadline = 0.08
        sent = []
        for i in range(1, 51):
            now = i * 0.1
            state.feed(bytes.fromhex("ff ff fe 00 00 00 00 00 00 00 00"), now)
            state.advance(now, sent.append)
        self.assertEqual(sent, [])
        state.advance(5.29, sent.append)
        self.assertEqual(sent, [])
        state.advance(5.38, sent.append)
        self.assertEqual(sent, [bytes.fromhex("60 00 19 00 4b 0d 0a")])

    def test_protocol_failure_is_not_retried_by_session_initializer(self):
        client = Replay().client
        client.reset = mock.Mock()
        client.connect_plate_only = mock.Mock()
        client.start_streams = mock.Mock(side_effect=PlateInitializationError("réponse absente"))
        with self.assertRaises(PlateInitializationError):
            client.initialize_session(1, 1, 0, attempts=3)
        client.start_streams.assert_called_once()


if __name__ == "__main__":
    unittest.main()
