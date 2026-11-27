import socket
import struct
import unittest
from unittest import mock
from scripts.kinvent_dual_hci import DualKinventClient, HCI_ACLDATA_PKT


class KernelTimestampTest(unittest.TestCase):
    def setUp(self):
        self.client = DualKinventClient(0, 'E8:EB:1B:6F:A7:5F', 'E8:EB:1B:79:B1:AB', None, 0, 1)
        self.client.sock = mock.Mock()
        self.client.log_connection_diagnostic = mock.Mock()
        self.client.plates[0].handle = 1
        self.client.by_handle[1] = self.client.plates[0]
        self.data = bytes([HCI_ACLDATA_PKT]) + struct.pack('<HH', 1, 1) + b'x'

    def test_recvmsg_preserves_packet_and_records_kernel_delay(self):
        self.client.kernel_timestamp_enabled = True
        self.client.sock.recvmsg.return_value = (self.data, [(socket.SOL_SOCKET, 64, struct.pack('=qq', 10, 0))], 0, None)
        with mock.patch('time.time_ns', return_value=10_002_000_000), mock.patch('time.monotonic_ns', return_value=2_000_000_000):
            self.assertEqual(self.client.receive(), (HCI_ACLDATA_PKT, self.data[1:]))
        stats = self.client.host_timings['attente_noyau_gauche']
        self.assertAlmostEqual(stats['seconds'], 0.002)
        self.assertEqual(stats['calls'], 1)
        self.client.sock.recv.assert_not_called()

    def test_unsupported_option_keeps_original_receive(self):
        self.client.sock.setsockopt.side_effect = OSError('unsupported')
        self.client.sock.recv.return_value = self.data
        with mock.patch('sys.platform', 'linux'):
            self.client.enable_kernel_timestamps()
        self.assertFalse(self.client.kernel_timestamp_enabled)
        self.assertEqual(self.client.receive(), (HCI_ACLDATA_PKT, self.data[1:]))
        self.client.sock.recvmsg.assert_not_called()

    def test_absent_truncated_and_invalid_timestamp_do_not_invent_zero_delay(self):
        for ancillary, flags in (([], 0), ([(socket.SOL_SOCKET,64,b'x')],0),
                                 ([(socket.SOL_SOCKET,64,struct.pack('=qq',10,0))],socket.MSG_CTRUNC),
                                 ([(socket.SOL_SOCKET,64,struct.pack('=qq',10,1_000_000_000))],0)):
            self.client.record_kernel_timestamp(self.data, ancillary, flags, 10_002_000_000, 2_000_000_000)
        self.assertEqual(self.client.host_timings, {})
        self.client.log_connection_diagnostic.assert_called_once()

    def test_clock_jump_and_future_timestamp_are_excluded(self):
        ancillary = [(socket.SOL_SOCKET, 64, struct.pack('=qq',10,0))]
        self.client.kernel_clock_offset = 8_000_000_000
        self.client.record_kernel_timestamp(self.data, ancillary, 0, 11_000_000_000, 2_000_000_000)
        self.assertEqual(self.client.host_timings, {})
        self.client.kernel_clock_offset = None
        self.client.record_kernel_timestamp(self.data, ancillary, 0, 9_000_000_000, 2_000_000_000)
        self.assertEqual(self.client.host_timings, {})
