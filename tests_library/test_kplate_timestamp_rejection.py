"""Kinvent #34217 : un horodatage invalide exclut le paquet, pas le flux."""
import unittest
from unittest.mock import Mock

from scripts.kinvent_dual_hci import DualKinventClient, PlateState, UART_VALUE_HANDLE


def frame(timestamp):
    return b'\xff\xff\xfe' + timestamp.to_bytes(2, 'big') + bytes(12)


class TimestampRejectionTest(unittest.TestCase):
    def plate(self):
        plate = PlateState('droite', '00:00:00:00:00:01', 0)
        plate.offsets = dict.fromkeys(('av_d', 'av_g', 'ar_d', 'ar_g'), 0)
        return plate

    def test_stale_packet_preserves_latest_force_queue_and_clock(self):
        plate = self.plate()
        plate.decode(frame(3000))
        latest = plate.latest
        origin = plate.timeline_host_origin
        for timestamp in (2960, 640, 13001):
            self.assertIsNone(plate.decode(frame(timestamp)))
            self.assertIs(plate.latest, latest)
            self.assertEqual(plate.last_sensor_time, 3000)
            self.assertEqual(plate.timeline_host_origin, origin)
        self.assertEqual(plate.measurements, 1)
        self.assertEqual(len(plate.samples), 1)
        self.assertEqual(plate.rejected_timestamps, 3)
        self.assertIsNotNone(plate.decode(frame(3040)))
        self.assertEqual(plate.measurements, 2)
        self.assertAlmostEqual(plate.samples[-1]['sample_monotonic'] - origin, .04)

    def test_invalid_packet_does_not_enter_tare(self):
        plate = self.plate()
        plate.sample_monotonic(3000, 1)
        plate.offsets = None
        self.assertIsNone(plate.decode(frame(2960)))
        self.assertFalse(plate.tare_samples)
        self.assertIsNone(plate.tare_started_at)

    def test_valid_packets_after_error_in_same_notification_are_processed(self):
        client = DualKinventClient(0, '00:00:00:00:00:01',
                                  '00:00:00:00:00:02', None, 0, 1)
        plate = client.plates[1]
        plate.handle = 1
        plate.offsets = dict.fromkeys(('av_d', 'av_g', 'ar_d', 'ar_g'), 0)
        client.by_handle[1] = plate
        client.pair_samples = Mock()
        baseline = client.stream_counters()
        values = b''.join(frame(t) for t in (3000, 3040, 3000, 3080, 3120))
        values += b'\xff\xff\xfe'
        client.handle_att(1, b'\x1b' + UART_VALUE_HANDLE.to_bytes(2, 'little') + values)
        self.assertEqual([entry['sample']['t'] for entry in plate.samples],
                         [3000, 3040, 3080, 3120])
        self.assertEqual(client.pair_samples.call_count, 4)
        self.assertEqual(plate.handle, 1)
        self.assertEqual(plate.measurements, 4)
        report = client.stream_counters()['plates']['droite']
        self.assertEqual(report['rejets_chronologie'] - baseline['plates']['droite']['rejets_chronologie'], 1)

    def test_unrelated_runtime_failure_is_not_hidden(self):
        plate = self.plate()
        plate.sample_monotonic = Mock(side_effect=RuntimeError('erreur indépendante'))
        with self.assertRaisesRegex(RuntimeError, 'erreur indépendante'):
            plate.decode(frame(3000))
