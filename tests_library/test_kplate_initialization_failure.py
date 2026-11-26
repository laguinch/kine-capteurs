import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.kinvent_dual_hci import DualKinventClient, PlateInitializationError


class InitializationFailureTest(unittest.TestCase):
    def test_protocol_failure_cleans_up_and_does_not_retry_same_request(self):
        for already_open in (False, True):
            with self.subTest(already_open=already_open), tempfile.TemporaryDirectory() as directory:
                client = DualKinventClient(0, '00:00:00:00:00:01',
                                          '00:00:00:00:00:02', None, 0, 1)
                client.sock = Mock() if already_open else None
                client.open = Mock(side_effect=lambda: setattr(client, 'sock', Mock()))
                client.initialize_session = Mock(side_effect=PlateInitializationError('réponse fréquence absente'))
                client.disconnect_all = Mock()
                client.clear_connection_state = Mock()
                client.wait_for_reconnect_cooldown = Mock()
                client.close = Mock(side_effect=lambda: setattr(client, 'sock', None))
                states = []
                client.write_worker_state = lambda path, **state: states.append(state)
                command = json.dumps({'action': 'connect', 'generation': 'one-attempt'})
                with patch.object(Path, 'read_text', side_effect=[command, command, KeyboardInterrupt]), patch('scripts.kinvent_dual_hci.time.sleep'):
                    with self.assertRaises(KeyboardInterrupt):
                        client.run_persistent(1, 1, 0, Path(directory)/'control.json',
                                              Path(directory)/'state.json', managed=True)
                client.initialize_session.assert_called_once()
                self.assertGreaterEqual(client.disconnect_all.call_count, 1)
                self.assertIsNone(client.sock)
                self.assertEqual(states[-1]['phase'], 'disconnected')
                self.assertIn('réponse fréquence absente', states[-1]['error'])
                self.assertEqual(states[-1]['generation'], 'one-attempt')
                self.assertNotIn('error', [state['phase'] for state in states])
