import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.kinvent_dual_hci import DualKinventClient


class GameSessionTest(unittest.TestCase):
    def test_balance_session_uses_no_terminal_silence_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'control.json'
            control.write_text(json.dumps({'action': 'start', 'generation': 'game',
                                          'duration': 60, 'mode': 'balance',
                                          'csv_path': str(Path(directory) / 'test.csv')}))
            client = DualKinventClient(1, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)
            client.sock = Mock()
            for handle, plate in enumerate(client.plates):
                plate.handle = handle
                client.by_handle[handle] = plate
            for name in ('validate_live_streams', 'open_csv', 'close_csv',
                         'disconnect_all', 'close', 'consume_control_command',
                         'refresh_idle_worker_state'):
                setattr(client, name, Mock())
            client.finish_acquisition_streams = Mock(return_value=False)
            states = []
            client.write_worker_state = lambda path, **state: states.append(state)
            calls = []
            def pump(duration, **kwargs):
                if kwargs.get('progress'):
                    calls.append(kwargs)
                    self.assertIsNone(kwargs['stream_silence_timeout'])
                    client.paired_samples = 2
                    control.write_text(json.dumps({'action': 'disconnect',
                                                    'generation': 'stop',
                                                    'managed_shutdown': True}))
                return True
            client.pump = pump
            client.run_persistent(1, 1, 0, control, Path(directory) / 'state.json', managed=True)
            client.refresh_idle_worker_state.assert_called_once_with(Path(directory) / 'state.json')
            self.assertEqual(len(calls), 1)
            self.assertIn('active', [s['phase'] for s in states])
            self.assertIn('idle', [s['phase'] for s in states])
            self.assertNotIn('degraded', [s['phase'] for s in states])


if __name__ == '__main__':
    unittest.main()
