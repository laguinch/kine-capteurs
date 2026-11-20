"""Réponses HCI capturées et entrelacements des attentes du pilote."""
import struct
import unittest
from unittest.mock import Mock, patch

from scripts import kinvent_dual_hci as driver
from ble.kinvent.kplates.initialization import PlateInitialization


class HciEventsTest(unittest.TestCase):
    def setUp(self):
        self.client = driver.DualKinventClient(
            1, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)
        self.plate = self.client.plates[0]
        self.plate.handle = 12
        self.client.by_handle[12] = self.plate
        self.client.send_command = lambda ogf, ocf, params: (ogf << 10) | ocf

    def packets(self, *hex_packets):
        self.client.receive = Mock(side_effect=[
            (driver.HCI_EVENT_PKT, bytes.fromhex(p)) for p in hex_packets])

    def test_captured_zero_update_does_not_complete_request(self):
        # Capture double du 15 juin : résultat nul puis intervalle 7,5 ms.
        self.packets('0f 04 00 01 13 20',
                     '3e 0a 03 00 0c 00 00 00 00 00 00 00',
                     '3e 0a 03 00 0c 00 06 00 00 00 f4 01')
        self.client.update_connection_interval(self.plate, 6, 6, 500)
        self.assertEqual(self.client.receive.call_count, 3)

    def test_update_before_command_status_is_preserved(self):
        self.packets('3e 0a 03 00 0c 00 06 00 00 00 f4 01',
                     '0f 04 00 01 13 20')
        self.client.update_connection_interval(self.plate, 6, 6, 500)
        self.assertEqual(self.client.receive.call_count, 2)

    def test_other_plate_update_cannot_complete_request(self):
        self.packets('0f 04 00 01 13 20',
                     '3e 0a 03 00 0a 00 06 00 00 00 f4 01',
                     '3e 0a 03 00 0c 00 06 00 00 00 f4 01')
        self.client.update_connection_interval(self.plate, 6, 6, 500)
        self.assertEqual(len(self.client.pending_le_events), 1)
        self.assertEqual(self.client.pending_le_events[0][4], 10)

    def test_failed_update_before_status_is_reported(self):
        self.packets('3e 0a 03 0c 0c 00 00 00 00 00 00 00',
                     '0f 04 00 01 13 20')
        with self.assertRaisesRegex(RuntimeError, 'statut 0x0c'):
            self.client.update_connection_interval(self.plate, 6, 6, 500)

    def test_zero_update_alone_times_out(self):
        packets = iter([(driver.HCI_EVENT_PKT, bytes.fromhex(p)) for p in (
            '0f 04 00 01 13 20', '3e 0a 03 00 0c 00 00 00 00 00 00 00')])
        self.client.receive = lambda: next(packets, None)
        ticks = iter(i / 10 for i in range(100))
        with patch.object(driver.time, 'monotonic', side_effect=lambda: next(ticks)):
            with self.assertRaisesRegex(TimeoutError, 'sans réponse valide'):
                self.client.update_connection_interval(self.plate, 6, 6, 500, timeout=1)

    def test_legacy_and_enhanced_connection_before_status(self):
        for event in (
            '3e 13 01 00 00 00 00 00 5f a7 6f 1b eb e8 24 00 00 00 f4 01 00',
            '3e 1f 0a 00 00 00 00 00 5f a7 6f 1b eb e8 '
            '00 00 00 00 00 00 00 00 00 00 00 00 24 00 00 00 f4 01 00',
        ):
            with self.subTest(event=event):
                self.plate.handle = None
                self.packets(event, '0f 04 00 01 0d 20')
                self.client.connect(self.plate, 1)
                self.assertEqual(self.plate.handle, 0)
                self.assertIs(self.client.by_handle[0], self.plate)

    def test_failed_connection_without_peer_address_is_reported(self):
        self.plate.handle = None
        self.packets('3e 13 01 3e 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
                     '0f 04 00 01 0d 20')
        with self.assertRaisesRegex(RuntimeError, 'statut 0x3e'):
            self.client.connect(self.plate, 1)
        self.assertIsNone(self.plate.handle)

    def test_initialization_response_does_not_pollute_measurements(self):
        state = PlateInitialization('gauche')
        state.advance(0, lambda command: None)
        self.client._plate_initializations[12] = state
        self.plate.decode = Mock()
        self.client.handle_att(12, bytes.fromhex('1b 52 00') + b'KINVENT FW 2.34V')
        self.assertTrue(state.acknowledged)
        self.plate.decode.assert_not_called()
        self.assertEqual(self.plate.notifications, 0)
        self.client._plate_initializations.clear()
        self.plate.decode.return_value = None
        self.client.handle_att(12, bytes.fromhex('1b 52 00 ff ff fe fa 13 fa 1f 80 80 05 6e'))
        self.plate.decode.assert_called_once()
        self.assertEqual(self.plate.notifications, 1)

    def test_session_failure_preserves_controller_error_for_interface(self):
        self.client.reset = Mock(side_effect=TimeoutError('Pas de réponse HCI 0x0c03'))
        self.client.disconnect_all = Mock()
        with patch.object(driver.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Dernière erreur : Pas de réponse HCI 0x0c03') as caught:
                self.client.initialize_session(1, 1, 0, attempts=1)
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)
        self.client.reset.assert_called_once()

    def test_session_clear_discards_old_events(self):
        self.client.process((driver.HCI_EVENT_PKT, bytes.fromhex(
            '3e 0a 03 00 0c 00 06 00 00 00 f4 01')))
        self.client.clear_connection_state()
        self.assertFalse(self.client.pending_le_events)


if __name__ == '__main__':
    unittest.main()
