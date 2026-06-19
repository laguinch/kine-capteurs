import importlib.util
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kinvent_dual_hci.py"


class KPlateDualTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kinvent_dual_hci", SCRIPT_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_combines_left_and_right_load(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        left, right = client.plates
        left.latest = {"force_kg": 60.0, "t": 1}
        right.latest = {"force_kg": 40.0, "t": 2}
        left.distribution = {"cop_x": 0.0, "cop_y": 0.2}
        right.distribution = {"cop_x": 0.0, "cop_y": -0.1}

        values = client.combined_values()

        self.assertEqual(values["total_kg"], 100.0)
        self.assertEqual(values["left_pct"], 60.0)
        self.assertEqual(values["right_pct"], 40.0)
        self.assertEqual(values["asymmetry"], -20.0)
        self.assertAlmostEqual(values["global_x"], -0.2)
        self.assertAlmostEqual(values["global_y"], 0.08)

    def test_ignores_small_negative_zero_drift(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        left, right = client.plates
        left.latest = {"force_kg": -0.05, "t": 1}
        right.latest = {"force_kg": 0.03, "t": 2}

        values = client.combined_values()

        self.assertEqual(values["left_kg"], 0.0)
        self.assertEqual(values["right_kg"], 0.03)
        self.assertIsNone(values["left_pct"])
        self.assertIsNone(values["global_x"])

    def test_pairs_each_sample_once_within_tolerance(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
            sync_tolerance_ms=20,
        )
        left, right = client.plates
        base = time.monotonic()
        sample = {"force_kg": 50.0, "t": 1}
        left.samples.append(
            {
                "received_monotonic": base,
                "sample_monotonic": base,
                "received_utc": "2026-06-18T00:00:00+00:00",
                "sample": sample,
                "distribution": None,
            }
        )
        right.samples.append(
            {
                "received_monotonic": base + 0.012,
                "sample_monotonic": base + 0.012,
                "received_utc": "2026-06-18T00:00:00.012000+00:00",
                "sample": sample,
                "distribution": None,
            }
        )
        pairs = []
        client.write_combined = lambda left_entry, right_entry, delta: pairs.append(
            delta
        )

        client.pair_samples()
        client.pair_samples()

        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(pairs[0], 12.0, places=3)
        self.assertEqual(client.paired_samples, 1)
        self.assertFalse(left.samples)
        self.assertFalse(right.samples)

    def test_discards_sample_outside_tolerance(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
            sync_tolerance_ms=20,
        )
        left, right = client.plates
        base = time.monotonic()
        entry = {
            "received_utc": "2026-06-18T00:00:00+00:00",
            "sample": {"force_kg": 0.0, "t": 1},
            "distribution": None,
        }
        left.samples.append(
            {
                **entry,
                "received_monotonic": base,
                "sample_monotonic": base,
            }
        )
        right.samples.append(
            {
                **entry,
                "received_monotonic": base + 0.1,
                "sample_monotonic": base + 0.1,
            }
        )

        client.pair_samples()

        self.assertEqual(client.dropped_samples["gauche"], 1)
        self.assertFalse(left.samples)
        self.assertTrue(right.samples)

    def test_sensor_clock_reconstructs_regular_timeline_from_bursts(self):
        plate = self.module.PlateState(
            "gauche",
            "E8:EB:1B:6F:A7:5F",
            tare_duration=0,
        )

        first = plate.sample_monotonic(1000, 50.000)
        second = plate.sample_monotonic(1013, 50.045)
        third = plate.sample_monotonic(1027, 50.046)

        self.assertAlmostEqual(first, 50.000, places=6)
        self.assertAlmostEqual(second, 50.013, places=6)
        self.assertAlmostEqual(third, 50.027, places=6)

    def test_sensor_clock_recovers_when_stream_clock_resets(self):
        plate = self.module.PlateState(
            "gauche",
            "E8:EB:1B:6F:A7:5F",
            tare_duration=0,
        )

        plate.sample_monotonic(9000, 50.000)
        reset = plate.sample_monotonic(100, 51.000)
        following = plate.sample_monotonic(113, 51.020)

        self.assertAlmostEqual(reset, 51.000, places=6)
        self.assertAlmostEqual(following, 51.013, places=6)

    def test_keepalive_is_disabled_during_measurements(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )

        self.assertIsNone(client.keepalive_interval)

    def test_disconnect_identifies_plate_and_clears_handle(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[0]
        plate.handle = 0x10
        client.by_handle[0x10] = plate
        payload = bytes(
            [
                self.module.EVT_DISCONN_COMPLETE,
                0x04,
                0x00,
                0x10,
                0x00,
                0x08,
            ]
        )

        with self.assertRaises(self.module.PlateDisconnected) as context:
            client.process((self.module.HCI_EVENT_PKT, payload))

        self.assertIs(context.exception.plate, plate)
        self.assertEqual(context.exception.reason, 0x08)
        self.assertIsNone(plate.handle)
        self.assertNotIn(0x10, client.by_handle)

    def test_keeps_requested_adapter_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            bluetooth = Path(directory)
            (bluetooth / "hci1").mkdir()
            with mock.patch.object(self.module, "BLUETOOTH_SYSFS", bluetooth):
                selected = self.module.resolve_hci_adapter(1, timeout=0)

        self.assertEqual(selected, 1)

    def test_uses_renumbered_external_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            bluetooth = Path(directory)
            (bluetooth / "hci0").mkdir()
            (bluetooth / "hci2").mkdir()
            with mock.patch.object(self.module, "BLUETOOTH_SYSFS", bluetooth):
                selected = self.module.resolve_hci_adapter(1, timeout=0)

        self.assertEqual(selected, 2)

    def test_uses_hci0_when_external_adapter_disappears(self):
        with tempfile.TemporaryDirectory() as directory:
            bluetooth = Path(directory)
            (bluetooth / "hci0").mkdir()
            with mock.patch.object(self.module, "BLUETOOTH_SYSFS", bluetooth):
                selected = self.module.resolve_hci_adapter(1, timeout=0)

        self.assertEqual(selected, 0)

    def test_saves_and_reuses_tare(self):
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "tare.json"
            client = self.module.DualKinventClient(
                1,
                "E8:EB:1B:6F:A7:5F",
                "E8:EB:1B:79:B1:AB",
                None,
                2,
                1,
                calibration_path=calibration,
            )
            client.plates[0].offsets = {
                "av_d": 1,
                "av_g": 2,
                "ar_g": 3,
                "ar_d": 4,
            }
            client.plates[1].offsets = {
                "av_d": 5,
                "av_g": 6,
                "ar_g": 7,
                "ar_d": 8,
            }
            client.save_calibration()

            reused = self.module.DualKinventClient(
                1,
                "E8:EB:1B:6F:A7:5F",
                "E8:EB:1B:79:B1:AB",
                None,
                2,
                1,
                calibration_path=calibration,
            )

        self.assertTrue(reused.calibration_saved)
        self.assertEqual(reused.plates[0].offsets["av_d"], 1)
        self.assertEqual(reused.plates[1].offsets["ar_d"], 8)

    def test_retries_disconnect_during_initial_connection(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[0]
        attempts = []
        client.scan_for = lambda current, timeout: attempts.append("scan")
        client.connect = lambda current, timeout: setattr(current, "handle", 0x10)
        client.start_stream = lambda current, delay: attempts.append("stream")

        def pump(duration):
            if attempts.count("scan") == 1:
                plate.handle = None
                raise self.module.PlateDisconnected(plate, 0x3E)

        client.pump = pump
        client.connect_and_start_plate(plate, 1, 1, 0)

        self.assertEqual(attempts.count("scan"), 2)
        self.assertEqual(attempts.count("stream"), 1)

    def test_rearms_both_streams_after_connections(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        started = []

        def start_stream(plate, delay):
            started.append(plate.side)

        def pump(duration):
            for plate in client.plates:
                plate.notifications += 1

        client.start_stream = start_stream
        client.pump = pump
        client.ensure_streams_ready()

        self.assertEqual(started, [])

    def test_rearms_only_silent_stream(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        started = []
        pump_count = 0

        def start_stream(plate, delay):
            started.append(plate.side)

        def pump(duration):
            nonlocal pump_count
            pump_count += 1
            client.plates[1].notifications += 1
            if pump_count > 1:
                client.plates[0].notifications += 1

        client.start_stream = start_stream
        client.pump = pump
        client.ensure_streams_ready()

        self.assertEqual(started, ["gauche"])

    def test_reinitializes_whole_session_when_one_stream_stays_silent(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        resets = []
        connections = []
        readiness_checks = 0
        client.reset = lambda: resets.append(True)
        client.connect_and_start_plate = (
            lambda plate, scan, connect, delay: connections.append(plate.side)
        )

        def ensure_ready():
            nonlocal readiness_checks
            readiness_checks += 1
            if readiness_checks == 1:
                raise RuntimeError("flux silencieux")

        client.ensure_streams_ready = ensure_ready
        client.initialize_session(1, 1, 0, attempts=2)

        self.assertEqual(len(resets), 2)
        self.assertEqual(
            connections,
            ["gauche", "droite", "gauche", "droite"],
        )

    def test_initializes_each_plate_completely_in_sequence(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        events = []
        client.reset = lambda: events.append("reset")
        client.connect_and_start_plate = (
            lambda plate, scan, connect, delay: events.append(
                f"ready-{plate.side}"
            )
        )
        client.ensure_streams_ready = lambda: events.append("ready")

        client.initialize_session(1, 1, 0, attempts=1)

        self.assertEqual(
            events,
            [
                "reset",
                "ready-gauche",
                "ready-droite",
                "ready",
            ],
        )

    def test_idle_keepalive_uses_lightweight_command(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        sent = []
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        client.send_write_command = (
            lambda plate, value: sent.append((plate.side, value))
        )

        for plate in client.plates:
            client.send_write_command(plate, b"\xff")

        self.assertEqual(
            sent,
            [("gauche", b"\xff"), ("droite", b"\xff")],
        )

    def test_disconnects_both_plates_before_closing(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        sent = []
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
            client.by_handle[index] = plate
        client.sock = object()
        client.send_command = (
            lambda ogf, ocf, params: sent.append((ogf, ocf, params)) or 1
        )
        client.wait_for_command = lambda opcode, timeout: None
        client.receive = lambda: None

        with mock.patch.object(self.module.time, "sleep"):
            client.disconnect_all(timeout=0)

        self.assertEqual(len(sent), 2)
        self.assertTrue(all(plate.handle is None for plate in client.plates))
        self.assertEqual(sent[0][0], self.module.OGF_LINK_CTL)
        self.assertEqual(sent[0][1], self.module.OCF_DISCONNECT)

    def test_pump_stops_test_on_disconnect(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[0]
        packets = [(self.module.HCI_EVENT_PKT, b"")]

        def receive():
            return packets.pop() if packets else None

        def process(packet):
            raise self.module.PlateDisconnected(plate, 0x08)

        client.receive = receive
        client.process = process
        with self.assertRaisesRegex(
            RuntimeError,
            "test est arrêté",
        ):
            client.pump(0.01, progress=True)

    def test_connection_uses_observed_stable_parameters(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[0]
        sent = []
        client.send_command = (
            lambda ogf, ocf, params: sent.append(params) or 1
        )
        client.wait_for_command = lambda opcode: None
        client.receive = lambda: None

        with self.assertRaises(TimeoutError):
            client.connect(plate, timeout=0)

        values = struct.unpack("<HHBB6sBHHHHHH", sent[0])
        self.assertEqual(values[6], 0x0018)
        self.assertEqual(values[7], 0x0028)
        self.assertEqual(values[9], 0x01F4)

    def test_waits_for_cooldown_before_manual_reconnection(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        client.reconnect_not_before = time.monotonic() + 5.0

        with mock.patch.object(self.module.time, "sleep") as sleep:
            client.wait_for_reconnect_cooldown()

        self.assertGreaterEqual(sleep.call_args.args[0], 4.0)


if __name__ == "__main__":
    unittest.main()
