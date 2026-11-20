import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.kinvent_dual_hci import DualKinventClient
from ble.kinvent.kplates.acquisition_service import DualPlateAcquisitionService


class WorkerHeartbeatTest(unittest.TestCase):
    def test_idle_and_degraded_keep_details_and_become_fresh(self):
        for phase in ('idle', 'degraded'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'state.json'
                state = {'pid': os.getpid(), 'phase': phase,
                         'updated_at': '2000-01-01T00:00:00+00:00',
                         'generation': 'session', 'connected_sides': ['droite'],
                         'error': 'flux gauche absent', 'csv_path': 'test.csv',
                         'paired_samples': 12}
                path.write_text(json.dumps(state))
                self.assertFalse(DualPlateAcquisitionService._worker_state_is_fresh(state))
                DualKinventClient.refresh_idle_worker_state(path)
                refreshed = json.loads(path.read_text())
                self.assertTrue(DualPlateAcquisitionService._worker_state_is_fresh(refreshed))
                refreshed.pop('updated_at')
                state.pop('updated_at')
                self.assertEqual(refreshed, state)

    def test_never_refreshes_another_process_or_terminal_state(self):
        for state in ({'pid': -1, 'phase': 'idle'},
                      {'pid': os.getpid(), 'phase': 'disconnected'},
                      {'pid': os.getpid(), 'phase': 'error'}, []):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'state.json'
                original = json.dumps(state)
                path.write_text(original)
                DualKinventClient.refresh_idle_worker_state(path)
                self.assertEqual(path.read_text(), original)
