"""L'ordre des trames doit rester celui du flux, y compris s'il est invalide."""
import struct
import unittest
from scripts.kinvent_dual_hci import DualKinventClient
from ble.kinvent.kplates.protocol import MeasurementFramer, parse_frame


def frame(timestamp):
    return b'\xff\xff\xfe' + timestamp.to_bytes(2, 'big') + bytes(12)


class FragmentOrderTest(unittest.TestCase):
    def test_every_uart_split_preserves_backwards_input_without_reordering(self):
        times = [13865, 13905, 11585, 11625, 11665]
        stream = b''.join(map(frame, times)) + b'\xff\xff\xfe'
        for split in range(len(stream) + 1):
            with self.subTest(split=split):
                framer = MeasurementFramer()
                output = framer.feed(stream[:split]) + framer.feed(stream[split:])
                self.assertEqual([parse_frame(f)['t'] for f in output], times)

    def test_interleaved_acl_fragments_keep_each_platform_order(self):
        times = [13865, 13905, 11585, 11625, 11665]
        uart = b''.join(map(frame, times)) + b'\xff\xff\xfe'
        att = b'\x1b\x52\x00' + uart
        l2cap = struct.pack('<HH', len(att), 4) + att
        for split in range(4, len(l2cap)):
            with self.subTest(split=split):
                client = DualKinventClient(0, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)
                for handle in (10, 12):
                    payload = struct.pack('<HH', handle | 0x2000, split) + l2cap[:split]
                    self.assertEqual(client.extract_att(payload), (None, None))
                for handle in (12, 10):
                    tail = l2cap[split:]
                    actual_handle, rebuilt = client.extract_att(struct.pack('<HH', handle | 0x1000, len(tail)) + tail)
                    self.assertEqual(actual_handle, handle)
                    self.assertEqual(rebuilt, att)
                    self.assertEqual([parse_frame(f)['t'] for f in MeasurementFramer().feed(rebuilt[3:])], times)
