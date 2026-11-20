"""Reconstitution du flux, avec données synthétiques sans mesures patient."""
import unittest
from unittest.mock import Mock

from ble.kinvent.kplates.protocol import MeasurementFramer, parse_frame
from scripts.kinvent_dual_hci import DualKinventClient


def frame(t):
    return b'\xff\xff\xfe' + t.to_bytes(2, 'big') + bytes.fromhex('00 80 00') * 4


class MeasurementFramingTest(unittest.TestCase):
    def test_grouped_two_and_five_measurements(self):
        for count in (2, 5):
            with self.subTest(count=count):
                framer = MeasurementFramer()
                frames = [frame(40 * i) for i in range(count)]
                self.assertEqual(framer.feed(b''.join(frames)), frames[:-1])
                self.assertEqual(framer.feed(frame(40 * count)[:3]), frames[-1:])

    def test_every_possible_notification_split_preserves_frames(self):
        frames = [frame(40 * i) for i in range(8)]
        stream = b''.join(frames) + MeasurementFramer.marker
        for width in range(1, 86):
            with self.subTest(width=width):
                framer = MeasurementFramer()
                decoded = []
                for start in range(0, len(stream), width):
                    decoded.extend(framer.feed(stream[start:start + width]))
                self.assertEqual(decoded, frames)
                self.assertEqual([parse_frame(f)['t'] for f in decoded], list(range(0, 320, 40)))

    def test_control_messages_and_invalid_lengths_are_not_measurements(self):
        framer = MeasurementFramer()
        stream = (b'KINVENT FW 2.34V' + b'F=25Hz\x00\x00' +
                  b'\xff\xff\xfe' + bytes(8) + frame(40) +
                  b'\xff\xff\xfe' + bytes(15) + frame(80) +
                  MeasurementFramer.marker)
        self.assertEqual(framer.feed(stream), [frame(40), frame(80)])

    def test_oversized_garbage_does_not_grow_buffer(self):
        framer = MeasurementFramer()
        for value in (bytes(10000), MeasurementFramer.marker + bytes(10000)):
            self.assertEqual(framer.feed(value), [])
            self.assertLessEqual(len(framer.buffer), 19)
        self.assertEqual(framer.feed(frame(40) + MeasurementFramer.marker), [frame(40)])

    def test_each_plate_has_its_own_buffer_and_delivers_every_sample(self):
        client = DualKinventClient(1, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)
        client.pair_samples = Mock()
        for handle, plate in enumerate(client.plates):
            plate.handle = handle
            plate.offsets = {'av_d': 0, 'av_g': 0, 'ar_g': 0, 'ar_d': 0}
            client.by_handle[handle] = plate
        def notify(handle, data):
            client.handle_att(handle, b'\x1b\x52\x00' + data)
        notify(0, frame(40)[:9])
        notify(1, frame(100) + frame(140) + MeasurementFramer.marker)
        self.assertEqual([p.measurements for p in client.plates], [0, 2])
        notify(0, frame(40)[9:] + frame(80) + MeasurementFramer.marker)
        self.assertEqual([p.measurements for p in client.plates], [2, 2])
        self.assertEqual(client.pair_samples.call_count, 4)
        for plate in client.plates:
            self.assertEqual(plate.rejected_frames, 0)
            self.assertAlmostEqual(plate.samples[1]['sample_monotonic'] -
                                   plate.samples[0]['sample_monotonic'], 0.04)
        client.clear_connection_state()
        self.assertTrue(all(not p.measurement_framer.buffer for p in client.plates))


if __name__ == '__main__':
    unittest.main()
