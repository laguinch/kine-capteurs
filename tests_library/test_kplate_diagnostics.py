import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from scripts.kinvent_dual_hci import DualKinventClient, PlateState


class StreamDiagnosticsTest(unittest.TestCase):
    def client(self):
        return DualKinventClient(1, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)

    def test_deltas_distinguish_received_samples_from_sync_losses(self):
        client = self.client()
        baseline = client.stream_counters()
        left, right = client.plates
        left.notifications = 100
        left.measurements = 125
        left.uart_bytes = 2125
        left.last_notification_at = 14.9
        right.notifications = 20
        right.measurements = 25
        right.last_notification_at = 12
        client.paired_samples = 25
        client.dropped_samples['gauche'] = 90
        left.queue_overflows = 10
        output = io.StringIO()
        with redirect_stdout(output):
            current = client.log_stream_diagnostics(baseline, 10, 15)
        report = json.loads(output.getvalue().removeprefix('DIAG_FLUX '))
        self.assertEqual(report['paires_par_s'], 5)
        self.assertEqual(report['plates']['gauche']['mesures_par_s'], 25)
        self.assertEqual(report['plates']['gauche']['ecartees_sync'], 90)
        self.assertEqual(report['plates']['gauche']['debordements_file'], 10)
        self.assertEqual(report['plates']['droite']['age_notification_ms'], 3000)
        with redirect_stdout(io.StringIO()):
            client.log_stream_diagnostics(current, 15, 20)
        self.assertEqual(client.latest_stream_diagnostics['paires'], 0)
        self.assertEqual(client.latest_stream_diagnostics['plates']['gauche']['mesures'], 0)

    def test_queue_overflow_counts_only_evicted_samples(self):
        plate = PlateState('gauche', 'E8:EB:1B:6F:A7:5F', 0, buffer_size=2)
        plate.offsets = {'av_d': 0, 'av_g': 0, 'ar_d': 0, 'ar_g': 0}
        for timestamp in (0, 40, 80):
            plate.decode(b'\xff\xff\xfe' + timestamp.to_bytes(2, 'big') + bytes(12))
        self.assertEqual(plate.measurements, 3)
        self.assertEqual(plate.queue_overflows, 1)
        self.assertEqual([s['sample']['t'] for s in plate.samples], [40, 80])

    def test_final_summary_survives_acquisition_failure(self):
        client = self.client()
        client._pump = Mock(side_effect=RuntimeError('déconnexion simulée'))
        with redirect_stdout(io.StringIO()), patch('scripts.kinvent_dual_hci.time.monotonic', side_effect=[10, 12]):
            with self.assertRaisesRegex(RuntimeError, 'déconnexion simulée'):
                client.pump(60, progress=True)
        self.assertEqual(client.latest_stream_diagnostics['bilan'], 'final')
        self.assertEqual(client.latest_stream_diagnostics['duree_s'], 2)
        client._pump.assert_called_once()


if __name__ == '__main__':
    unittest.main()
