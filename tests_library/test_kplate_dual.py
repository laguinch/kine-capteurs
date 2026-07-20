import importlib.util
import asyncio
import csv
import json
import struct
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts.kinvent_kplates_bumble import KPlatesBumbleClient, build_parser


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

    def test_consumes_finished_start_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(
                json.dumps(
                    {
                        "action": "start",
                        "generation": "finished-test",
                        "duration": 60,
                    }
                ),
                encoding="utf-8",
            )

            self.module.DualKinventClient.consume_control_command(
                path,
                "finished-test",
            )
            command = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(command["action"], "idle")
        self.assertEqual(command["generation"], "finished-test")

    def test_keeps_newer_control_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(
                json.dumps(
                    {
                        "action": "start",
                        "generation": "new-test",
                    }
                ),
                encoding="utf-8",
            )

            self.module.DualKinventClient.consume_control_command(
                path,
                "old-test",
            )
            command = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(command["action"], "start")
        self.assertEqual(command["generation"], "new-test")

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

    def test_cmj_keeps_each_unilateral_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmj.csv"
            client = self.module.DualKinventClient(
                1,
                "E8:EB:1B:6F:A7:5F",
                "E8:EB:1B:79:B1:AB",
                None,
                0,
                1,
            )
            client.open_csv(path, mode="cmj")
            left, right = client.plates
            base = time.monotonic()
            for plate, force, offset in (
                (left, 40.0, 0.0),
                (right, 42.0, 0.003),
            ):
                plate.latest = {"force_kg": force, "t": 100}
                plate.samples.append(
                    {
                        "received_monotonic": base + offset,
                        "sample_monotonic": base + offset,
                        "received_utc": "2026-06-22T08:00:00+00:00",
                        "sample": plate.latest,
                        "distribution": None,
                    }
                )
            client.write_cmj_event(right)
            left.latest = {"force_kg": 45.0, "t": 101}
            left.samples.append(
                {
                    "received_monotonic": base + 0.013,
                    "sample_monotonic": base + 0.013,
                    "received_utc": "2026-06-22T08:00:00.013+00:00",
                    "sample": left.latest,
                    "distribution": None,
                }
            )
            client.write_cmj_event(left)
            client.close_csv()

            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "droite")
        self.assertEqual(float(rows[0]["source_kg"]), 42.0)
        self.assertEqual(rows[1]["source"], "gauche")
        self.assertEqual(float(rows[1]["source_kg"]), 45.0)

    def test_cmj_does_not_repeat_stale_opposite_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmj-stale.csv"
            client = self.module.DualKinventClient(
                1,
                "E8:EB:1B:6F:A7:5F",
                "E8:EB:1B:79:B1:AB",
                None,
                0,
                1,
            )
            client.open_csv(path, mode="cmj")
            left, right = client.plates
            base = time.monotonic()
            left.latest = {"force_kg": 35.0, "t": 100}
            right.latest = {"force_kg": 55.0, "t": 200}
            left.samples.append(
                {
                    "received_monotonic": base,
                    "sample_monotonic": base,
                    "received_utc": "2026-06-22T08:00:00+00:00",
                    "sample": left.latest,
                    "distribution": None,
                }
            )
            right.samples.append(
                {
                    "received_monotonic": base + 0.2,
                    "sample_monotonic": base + 0.2,
                    "received_utc": "2026-06-22T08:00:00.200+00:00",
                    "sample": right.latest,
                    "distribution": None,
                }
            )
            client.write_cmj_event(right)
            client.close_csv()
            with path.open(encoding="utf-8", newline="") as source:
                row = next(csv.DictReader(source))

        self.assertEqual(row["left_kg"], "")
        self.assertEqual(float(row["right_kg"]), 55.0)
        self.assertEqual(row["total_kg"], "")

    def test_detects_silent_measurement_stream(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        client.plates[0].last_notification_at = 10.0
        client.plates[1].last_notification_at = 11.5

        silent = client.silent_plate_sides(1.0, now=12.0)

        self.assertEqual(silent, ["gauche"])

    def test_missing_initial_stream_is_not_reported_as_usable(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index

        connected = client.connected_sides_except(["gauche"])

        self.assertEqual(connected, ["droite"])

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

    def test_keepalive_matches_official_ten_second_interval(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )

        self.assertEqual(client.keepalive_interval, 10.0)

    def test_game_keepalive_keeps_official_continuous_cadence(self):
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
        client.receive = lambda: None
        client.send_write_command = (
            lambda plate, value: sent.append((plate.side, value))
        )
        client.last_keepalive_at = (
            time.monotonic() - client.keepalive_interval - 0.1
        )

        client.pump(0.01, progress=True, show_progress=False)

        self.assertEqual(
            sent[:2],
            [("droite", b"\xff"), ("gauche", b"\xff")],
        )

    def test_cmj_parks_streams_after_acquisition(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        parked = []
        client.park_measurement_streams = (
            lambda commands=3: parked.append(commands)
        )

        streams_active = client.finish_acquisition_streams("cmj")

        self.assertFalse(streams_active)
        self.assertEqual(parked, [3])

    def test_balance_parks_streams_after_acquisition(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        parked = []
        client.park_measurement_streams = (
            lambda commands=3: parked.append(commands)
        )

        streams_active = client.finish_acquisition_streams("balance")

        self.assertFalse(streams_active)
        self.assertEqual(parked, [3])

    def test_idle_drop_preserves_remaining_connected_plate(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        states = []
        client.write_worker_state = (
            lambda path, **state: states.append(state)
        )
        disconnected = client.plates[0]
        disconnected.handle = None
        exc = self.module.PlateDisconnected(disconnected, 0x08)

        client.mark_full_reconnect_required(
            "state.json",
            "test-generation",
            {"csv_path": "jeu.csv", "mode": "balance"},
            exc,
        )

        self.assertIsNone(client.plates[0].handle)
        self.assertEqual(client.plates[1].handle, 0x11)
        self.assertEqual(states[0]["phase"], "degraded")
        self.assertEqual(states[0]["connected_sides"], ["droite"])
        self.assertIn("Reconnectez les plateformes", states[0]["error"])

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
                plate.measurements += 1

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
            client.plates[1].measurements += 1
            if pump_count > 1:
                client.plates[0].measurements += 1

        client.start_stream = start_stream
        client.pump = pump
        client.ensure_streams_ready()

        self.assertEqual(started, ["gauche"])

    def test_validates_fresh_synchronized_data_before_test(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index

        def pump(duration):
            for plate in client.plates:
                plate.notifications += 1
                plate.measurements += 1

        client.wake_measurement_streams = lambda: None
        client.pump = pump
        client.validate_live_streams()

        self.assertEqual(client.paired_samples, 0)

    def test_rejects_connected_but_silent_stream_before_test(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        wake_count = 0

        def wake():
            nonlocal wake_count
            wake_count += 1

        client.wake_measurement_streams = wake
        client.pump = lambda duration: None

        with self.assertRaisesRegex(RuntimeError, "Flux de mesure absent"):
            client.validate_live_streams()
        self.assertEqual(wake_count, 1)

    def test_wakes_both_measurement_streams_without_reconnecting(self):
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
        client.pump = lambda duration: None

        client.wake_measurement_streams()

        self.assertEqual(
            sent,
            [
                ("droite", b"\x90"),
                ("gauche", b"\x90"),
                ("droite", b"\x11"),
                ("gauche", b"\x11"),
            ],
        )

    def test_wake_uses_official_sequence_without_invented_delay(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        pumped = []
        sent = []
        client.pump = pumped.append
        client.send_write_command = (
            lambda plate, value: sent.append((plate.side, value))
        )

        client.wake_measurement_streams()

        self.assertEqual(pumped, [0.70, 0.25])
        self.assertEqual(
            sent,
            [
                ("droite", b"\x90"),
                ("gauche", b"\x90"),
                ("droite", b"\x11"),
                ("gauche", b"\x11"),
            ],
        )

    def test_parks_measurement_streams_without_disconnecting(self):
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
        client.pump = lambda duration: None

        client.park_measurement_streams()

        self.assertEqual(
            sent,
            [
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
            ],
        )
        self.assertTrue(all(plate.handle is not None for plate in client.plates))

    def test_single_park_command_keeps_handles_when_explicitly_requested(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        sent = []
        pumped = []
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        client.send_write_command = (
            lambda plate, value: sent.append((plate.side, value))
        )
        client.pump = lambda duration: pumped.append(duration)

        client.park_measurement_streams(commands=1)

        self.assertEqual(
            sent,
            [
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
            ],
        )
        self.assertEqual(pumped, [self.module.KPLATE_PARK_DELAY])

    def test_idle_session_rejects_stale_notifications(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
            plate.last_notification_at = time.monotonic() - 5

        with self.assertRaisesRegex(RuntimeError, "Liaison Bluetooth figée"):
            client.ensure_recent_notifications()

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
        client.connect_plate_only = (
            lambda plate, scan, connect: connections.append(plate.side)
        )
        client.start_streams = lambda plates, delay: None
        client.update_connection_interval = lambda plate, **kwargs: None

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
            ["droite", "gauche", "droite", "gauche"],
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
        client.connect_plate_only = (
            lambda plate, scan, connect: events.append(
                f"connected-{plate.side}"
            )
        )
        client.update_connection_interval = (
            lambda plate, **kwargs: events.append(
                f"radio-{plate.side}-{kwargs['interval_min']:04x}"
            )
        )
        client.start_streams = (
            lambda plates, delay: events.append(
                "streams-" + "-".join(plate.side for plate in plates)
            )
        )
        client.ensure_streams_ready = lambda: events.append("ready")

        client.initialize_session(1, 1, 0, attempts=1)

        self.assertEqual(
            events,
            [
                "reset",
                "connected-droite",
                "connected-gauche",
                "radio-droite-0024",
                "radio-gauche-0024",
                "streams-droite-gauche",
                "ready",
            ],
        )

    def test_persistent_connection_defers_stream_validation(self):
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
        client.connect_plate_only = (
            lambda plate, scan, connect: events.append(
                f"connected-{plate.side}"
            )
        )
        client.update_connection_interval = lambda plate, **kwargs: None
        client.start_streams = (
            lambda plates, delay: events.append("streams")
        )
        client.ensure_streams_ready = lambda: events.append("ready")

        client.initialize_session(
            1,
            1,
            0,
            attempts=1,
            require_measurements=False,
        )

        self.assertEqual(
            events,
            ["reset", "connected-droite", "connected-gauche", "streams"],
        )

    def test_connect_plate_uses_official_initial_radio_window(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[0]
        updates = []
        client.scan_for = lambda target, timeout: None
        client.connect = lambda target, timeout: setattr(target, "handle", 0x10)
        client.pump = lambda duration: None
        client.update_connection_interval = (
            lambda target, **kwargs: updates.append(kwargs)
        )

        client.connect_plate_only(plate, 1, 1)

        self.assertEqual(updates[0]["interval_min"], 0x0006)
        self.assertEqual(updates[0]["interval_max"], 0x0006)
        self.assertEqual(updates[0]["supervision_timeout"], 0x01F4)

    def test_initial_streams_can_settle_before_first_park(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for plate in client.plates:
            plate.offsets = {
                "av_d": 0,
                "av_g": 0,
                "ar_g": 0,
                "ar_d": 0,
            }
        pumped = []
        client.pump = pumped.append

        missing = client.settle_initial_streams()

        self.assertEqual(pumped, [2.0])
        self.assertEqual(missing, ["gauche", "droite"])

    def test_initial_settle_accepts_fresh_measurements(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for plate in client.plates:
            plate.offsets = {
                "av_d": 0,
                "av_g": 0,
                "ar_g": 0,
                "ar_d": 0,
            }

        def pump(duration):
            del duration
            for plate in client.plates:
                plate.notifications += 10
                plate.measurements += 8

        client.pump = pump

        self.assertEqual(client.settle_initial_streams(), [])

    def test_initial_settle_waits_for_both_tares_before_measuring(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        calls = []

        def pump(duration):
            calls.append(duration)
            if len(calls) == 2:
                for plate in client.plates:
                    plate.offsets = {
                        "av_d": 0,
                        "av_g": 0,
                        "ar_g": 0,
                        "ar_d": 0,
                    }
            if duration == 2.0:
                for plate in client.plates:
                    plate.notifications += 10
                    plate.measurements += 8

        client.pump = pump

        self.assertEqual(client.settle_initial_streams(), [])
        self.assertEqual(calls, [0.25, 0.25, 2.0])

    def test_initial_settle_rejects_incoherent_saved_tare(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        client.calibration_saved = True
        for plate in client.plates:
            plate.offsets = {
                "av_d": 0,
                "av_g": 0,
                "ar_g": 0,
                "ar_d": 0,
            }

        def pump(duration):
            del duration
            client.plates[0].notifications += 10
            client.plates[0].measurements += 8
            client.plates[0].latest = {"force_kg": 93.8}
            client.plates[1].notifications += 10
            client.plates[1].measurements += 8
            client.plates[1].latest = {"force_kg": 0.0}

        client.pump = pump

        with self.assertRaisesRegex(RuntimeError, "Tare enregistrée incohérente"):
            client.settle_initial_streams()

    def test_initial_settle_accepts_valid_saved_tare_at_rest(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        client.calibration_saved = True
        for plate in client.plates:
            plate.offsets = {
                "av_d": 0,
                "av_g": 0,
                "ar_g": 0,
                "ar_d": 0,
            }

        def pump(duration):
            del duration
            for plate in client.plates:
                plate.notifications += 10
                plate.measurements += 8
                plate.latest = {"force_kg": 0.0}

        client.pump = pump

        self.assertEqual(client.settle_initial_streams(), [])

    def test_rejected_frame_is_counted_for_diagnostics(self):
        plate = self.module.PlateState(
            "gauche",
            "E8:EB:1B:6F:A7:5F",
            0,
        )

        self.assertIsNone(plate.decode(b"\xff\xff\xfe\x00"))
        self.assertEqual(plate.rejected_frames, 1)

    def test_connects_right_plate_first_like_official_application(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )

        self.assertEqual(
            [plate.side for plate in client.connection_order()],
            ["droite", "gauche"],
        )

    def test_uses_plate_specific_stream_initialization_for_both_plates(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        writes = []
        cccd = []
        updates = []
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
        client.send_write_command = (
            lambda plate, value: writes.append((plate.side, value))
        )
        client.send_att = (
            lambda plate, value: cccd.append((plate.side, value))
        )
        client.wait_write_response = lambda plate: None
        client.update_connection_interval = (
            lambda plate: updates.append(plate.side)
        )
        client.pump = lambda duration: None

        client.start_streams(client.plates)

        left_values = [
            value for side, value in writes if side == "gauche"
        ]
        self.assertEqual(
            left_values,
            [
                b"\x10",
                b"\x10",
                b"\x09",
                b"\x76",
                b"\x11",
                b"\x10",
                b"\x10",
                bytes.fromhex("60 00 19 00 4b 0d 0a"),
                b"\x66",
                b"\x56",
                bytes.fromhex("ac 00 54 f8"),
                bytes.fromhex("ac 01 04 a9"),
                b"\x11",
            ],
        )
        self.assertEqual(updates, ["gauche", "droite"])
        self.assertEqual(len(cccd), 2)

    def test_connection_update_matches_official_final_radio_window(self):
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
        sent = []
        client.send_command = (
            lambda ogf, ocf, params: sent.append((ogf, ocf, params)) or 1
        )
        client.wait_for_command = lambda opcode: None
        client.receive = lambda: (
            self.module.HCI_EVENT_PKT,
            bytes.fromhex("3e 0a 03 00 10 00 18 00 00 00 00 02"),
        )

        client.update_connection_interval(plate)

        self.assertEqual(sent[0][1], self.module.OCF_LE_CONN_UPDATE)
        values = struct.unpack("<HHHHHHH", sent[0][2])
        self.assertEqual(values, (0x10, 0x0009, 0x0018, 0, 0x0200, 0, 0))

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

        client.send_keepalive()

        self.assertEqual(
            sent,
            [("droite", b"\xff"), ("gauche", b"\xff")],
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

    def test_shutdown_session_never_leaves_one_plate_connected(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        client.sock = object()
        for index, plate in enumerate(client.plates, start=0x10):
            plate.handle = index
            client.by_handle[index] = plate
        calls = []
        client.disconnect_all = lambda: calls.append("disconnect")
        client.close = lambda: calls.append("close")

        client.shutdown_session()

        self.assertEqual(calls, ["disconnect", "close"])
        self.assertTrue(all(plate.handle is None for plate in client.plates))
        self.assertFalse(client.by_handle)

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

    def test_idle_pump_preserves_raw_disconnect_reason(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        plate = client.plates[1]
        packets = [(self.module.HCI_EVENT_PKT, b"")]
        client.receive = lambda: packets.pop() if packets else None
        client.process = lambda packet: (
            (_ for _ in ()).throw(
                self.module.PlateDisconnected(plate, 0x08)
            )
        )

        with self.assertRaises(self.module.PlateDisconnected):
            client.pump(0.01, progress=False)

    def test_stop_command_wins_over_silent_stream_detection(self):
        client = self.module.DualKinventClient(
            1,
            "E8:EB:1B:6F:A7:5F",
            "E8:EB:1B:79:B1:AB",
            None,
            0,
            1,
        )
        for plate in client.plates:
            plate.last_notification_at = 1.0
        stop_requested = {"value": False}

        def receive():
            stop_requested["value"] = True
            return None

        client.receive = receive

        with mock.patch.object(
            self.module.time,
            "monotonic",
            side_effect=[10.0] * 10,
        ):
            completed = client.pump(
                30.0,
                progress=True,
                show_progress=False,
                stop_requested=lambda: stop_requested["value"],
                stream_silence_timeout=1.0,
            )

        self.assertFalse(completed)

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

    def test_bumble_stream_configuration_uses_official_radio_update(self):
        global_events = []

        class FakeConnection:
            def __init__(self, side):
                self.side = side
                self.updates = []

            async def update_parameters(self, *values):
                self.updates.append(values)
                global_events.append(("radio", self.side, values))

        global_writes = []
        uart_value_handle = self.module.UART_VALUE_HANDLE

        class FakeClient:
            def __init__(self, side):
                self.side = side
                self.writes = []

            async def write_value(self, handle, value, with_response=False):
                self.writes.append((handle, value, with_response))
                if handle == uart_value_handle and not with_response:
                    global_writes.append((self.side, value))
                    global_events.append(("uart", self.side, value))

        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            write_delay=0,
        )
        left, right = client.dual.plates
        left_connection = FakeConnection("gauche")
        right_connection = FakeConnection("droite")
        left_client = FakeClient("gauche")
        right_client = FakeClient("droite")
        client.connections = {
            right: right_connection,
            left: left_connection,
        }
        right.handle = 0x10
        left.handle = 0x11

        async def immediate_sleep(_delay):
            return None

        with mock.patch(
            "scripts.kinvent_kplates_bumble.asyncio.sleep",
            immediate_sleep,
        ):
            asyncio.run(
                client.configure_streams(
                    {
                        right: right_client,
                        left: left_client,
                    }
                )
            )

        self.assertEqual(
            right_connection.updates,
            [(0x0009, 0x0018, 0, 0x0200)],
        )
        self.assertEqual(
            left_connection.updates,
            [(0x0009, 0x0018, 0, 0x0200)],
        )
        self.assertIn(
            (self.module.UART_CCCD_HANDLE, b"\x01\x00", True),
            right_client.writes,
        )
        self.assertIn(
            (self.module.UART_CCCD_HANDLE, b"\x01\x00", True),
            left_client.writes,
        )
        self.assertEqual(
            [
                value
                for handle, value, with_response in left_client.writes
                if handle == self.module.UART_VALUE_HANDLE and not with_response
            ],
            [
                b"\x10",
                b"\x10",
                b"\x09",
                b"\x76",
                b"\x11",
                b"\x10",
                b"\x10",
                bytes.fromhex("60 00 19 00 4b 0d 0a"),
                b"\x66",
                b"\x56",
                bytes.fromhex("ac 00 54 f8"),
                bytes.fromhex("ac 01 04 a9"),
                b"\x11",
            ],
        )
        self.assertEqual(
            [
                value
                for handle, value, with_response in right_client.writes
                if handle == self.module.UART_VALUE_HANDLE and not with_response
            ],
            [
                b"\x10",
                b"\x10",
                b"\x09",
                b"\x76",
                b"\x11",
                b"\x10",
                b"\x10",
                bytes.fromhex("60 00 19 00 4b 0d 0a"),
                b"\x66",
                b"\x56",
                bytes.fromhex("ac 00 54 f8"),
                bytes.fromhex("ac 01 04 a9"),
                b"\x11",
            ],
        )
        self.assertEqual(
            global_writes,
            [
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
                ("droite", b"\x10"),
                ("gauche", b"\x10"),
                ("droite", b"\x09"),
                ("gauche", b"\x09"),
                ("droite", b"\x76"),
                ("gauche", b"\x76"),
                ("gauche", b"\x11"),
                ("droite", b"\x11"),
                ("gauche", b"\x10"),
                ("gauche", b"\x10"),
                ("droite", b"\x10"),
                ("droite", b"\x10"),
                ("gauche", bytes.fromhex("60 00 19 00 4b 0d 0a")),
                ("gauche", b"\x66"),
                ("droite", bytes.fromhex("60 00 19 00 4b 0d 0a")),
                ("droite", b"\x66"),
                ("gauche", b"\x56"),
                ("droite", b"\x56"),
                ("gauche", bytes.fromhex("ac 00 54 f8")),
                ("droite", bytes.fromhex("ac 00 54 f8")),
                ("gauche", bytes.fromhex("ac 01 04 a9")),
                ("droite", bytes.fromhex("ac 01 04 a9")),
                ("gauche", b"\x11"),
                ("droite", b"\x11"),
            ],
        )
        self.assertEqual(
            global_events[:6],
            [
                ("uart", "droite", b"\x10"),
                ("uart", "gauche", b"\x10"),
                ("radio", "droite", (0x0009, 0x0018, 0, 0x0200)),
                ("radio", "gauche", (0x0009, 0x0018, 0, 0x0200)),
                ("uart", "droite", b"\x10"),
                ("uart", "gauche", b"\x10"),
            ],
        )

    def test_bumble_connect_uses_official_create_connection_preferences(self):
        class FakeConnectionParametersPreferences:
            def __init__(
                self,
                connection_interval_min,
                connection_interval_max,
                max_latency,
                supervision_timeout,
                min_ce_length,
                max_ce_length,
            ):
                self.connection_interval_min = connection_interval_min
                self.connection_interval_max = connection_interval_max
                self.max_latency = max_latency
                self.supervision_timeout = supervision_timeout
                self.min_ce_length = min_ce_length
                self.max_ce_length = max_ce_length

        class FakeConnection:
            handle = 0x10

        class FakeDevice:
            def __init__(self):
                self.kwargs = None

            async def connect(self, remote_address, **kwargs):
                self.kwargs = kwargs
                return FakeConnection()

        class FakeAddress:
            PUBLIC_DEVICE_ADDRESS = 0
            RANDOM_DEVICE_ADDRESS = 1

            def __init__(self, value, address_type):
                self.value = value
                self.address_type = address_type

        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            write_delay=0,
        )
        plate = client.dual.plates[1]
        device = FakeDevice()

        with mock.patch.dict(
            sys.modules,
            {
                "bumble": types.SimpleNamespace(),
                "bumble.device": types.SimpleNamespace(
                    ConnectionParametersPreferences=FakeConnectionParametersPreferences
                ),
                "bumble.hci": types.SimpleNamespace(HCI_LE_1M_PHY=1),
            },
        ):
            asyncio.run(
                client.connect_plate(
                    device,
                    plate,
                    FakeAddress,
                    15.0,
                )
            )

        preferences = device.kwargs["connection_parameters_preferences"][1]
        self.assertEqual(preferences.connection_interval_min, 30)
        self.assertEqual(preferences.connection_interval_max, 50)
        self.assertEqual(preferences.max_latency, 0)
        self.assertEqual(preferences.supervision_timeout, 5000)
        self.assertEqual(preferences.min_ce_length, 0)
        self.assertEqual(preferences.max_ce_length, 0)
        self.assertEqual(device.kwargs["timeout"], 15.0)

    def test_bumble_notification_feeds_plate_decoder(self):
        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )
        left = client.dual.plates[0]
        frame = (
            b"\xff\xff\xfe"
            + (1).to_bytes(2, "big")
            + (35950).to_bytes(3, "big", signed=True)
            + (33500).to_bytes(3, "big", signed=True)
            + (34050).to_bytes(3, "big", signed=True)
            + (36050).to_bytes(3, "big", signed=True)
        )

        client.handle_payload(left, frame)

        self.assertEqual(left.notifications, 1)
        self.assertIsNotNone(left.latest)
        self.assertEqual(left.latest["force_kg"], 0.0)

    def test_bumble_wake_uses_official_plate_sequence(self):
        class FakeClient:
            def __init__(self):
                self.writes = []

            async def write_value(self, handle, value, with_response=False):
                self.writes.append((handle, value, with_response))

        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )
        left, right = client.dual.plates
        left.handle = 0x10
        right.handle = 0x11
        left_client = FakeClient()
        right_client = FakeClient()

        asyncio.run(
            client.wake_measurement_streams(
                {
                    left: left_client,
                    right: right_client,
                }
            )
        )

        self.assertEqual(
            [value for _, value, _ in left_client.writes],
            [b"\x90", b"\x11"],
        )
        self.assertEqual(
            [value for _, value, _ in right_client.writes],
            [b"\x90", b"\x11"],
        )

    def test_bumble_gatt_preflight_discovers_in_official_connection_order(self):
        class FakeClient:
            def __init__(self, label, events):
                self.label = label
                self.events = events

            async def discover_services(self):
                self.events.append(f"discover:{self.label}")

            async def read_value(self, handle):
                self.events.append((f"read:{self.label}", handle))
                return b"P104356"

        events = []
        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )
        left, right = client.dual.plates

        asyncio.run(
            client.run_official_gatt_preflight(
                {
                    right: FakeClient("droite", events),
                    left: FakeClient("gauche", events),
                }
            )
        )

        self.assertEqual(
            events[:2],
            ["discover:droite", "discover:gauche"],
        )
        self.assertCountEqual(
            events[2:],
            [
                ("read:droite", 0x0016),
                ("read:gauche", 0x0016),
            ],
        )

    def test_bumble_gatt_preflight_keeps_started_official_discovery(self):
        class FakeClient:
            def __init__(self, label, events):
                self.label = label
                self.events = events

            async def discover_services(self):
                self.events.append(f"discover:{self.label}")

            async def read_value(self, handle):
                self.events.append((f"read:{self.label}", handle))
                return b"P104356"

        async def run_preflight(client, right, right_client, left, left_client):
            right_discovery = asyncio.create_task(right_client.discover_services())
            await client.run_official_gatt_preflight(
                {
                    right: right_client,
                    left: left_client,
                },
                started_discoveries={right: right_discovery},
            )

        events = []
        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )
        left, right = client.dual.plates
        right_client = FakeClient("droite", events)
        left_client = FakeClient("gauche", events)

        asyncio.run(run_preflight(client, right, right_client, left, left_client))

        self.assertEqual(
            events.count("discover:droite"),
            1,
        )
        self.assertEqual(
            events[:2],
            ["discover:droite", "discover:gauche"],
        )

    def test_bumble_disconnect_logger_records_hci_reason(self):
        class FakeConnection:
            def __init__(self):
                self.callbacks = {}

            def on(self, event_name, callback):
                self.callbacks[event_name] = callback

        client = KPlatesBumbleClient(
            transport="usb:0",
            left_address="E8:EB:1B:6F:A7:5F",
            right_address="E8:EB:1B:79:B1:AB",
            address_type="public",
            csv_path=None,
            tare_duration=0,
        )
        left = client.dual.plates[0]
        connection = FakeConnection()

        client.register_disconnect_logger(connection, left)
        connection.callbacks["disconnection"](62)

        self.assertIn(left, client.disconnected)
        self.assertEqual(client.disconnect_reasons[left], 0x3E)
        self.assertIsNone(left.handle)

    def test_bumble_reconnects_initial_official_0x3e_once(self):
        class FakeConnection:
            def __init__(self, label):
                self.gatt_client = object()
                self.handle = 0x10 if label == "initial" else 0x11

            def on(self, event_name, callback):
                pass

        class FakeClient(KPlatesBumbleClient):
            def __init__(self):
                super().__init__(
                    transport="usb:0",
                    left_address="E8:EB:1B:6F:A7:5F",
                    right_address="E8:EB:1B:79:B1:AB",
                    address_type="public",
                    csv_path=None,
                    tare_duration=0,
                )
                self.discoveries = 0
                self.reconnects = 0
                self.settles = 0

            async def wait_official_gatt_settle(self, plate):
                self.settles += 1

            async def discover_official_services(self, plate, fake_client):
                self.discoveries += 1
                if self.discoveries == 1:
                    self.disconnect_reasons[plate] = 0x3E
                    self.disconnected.add(plate)
                    plate.handle = None
                    raise asyncio.CancelledError()

            async def connect_plate(self, device, plate, Address, connect_timeout):
                self.reconnects += 1
                plate.handle = 0x11
                self.disconnected.discard(plate)
                self.disconnect_reasons.pop(plate, None)
                return FakeConnection("reconnected")

        client = FakeClient()
        right = client.dual.plates[1]
        initial_connection = FakeConnection("initial")
        all_clients = {right: initial_connection.gatt_client}
        started_discoveries = {}

        asyncio.run(
            client.complete_initial_official_discovery(
                object(),
                right,
                object,
                initial_connection,
                all_clients,
                started_discoveries,
                15.0,
            )
        )

        self.assertEqual(client.discoveries, 2)
        self.assertEqual(client.reconnects, 1)
        self.assertEqual(client.settles, 2)
        self.assertIn(right, started_discoveries)
        self.assertNotIn(right, client.disconnected)
        self.assertEqual(right.handle, 0x11)

    def test_bumble_initial_preflight_waits_for_official_gatt_settle(self):
        class FakeConnection:
            def __init__(self):
                self.gatt_client = object()
                self.handle = 0x10

        class FakeClient(KPlatesBumbleClient):
            def __init__(self):
                super().__init__(
                    transport="usb:0",
                    left_address="E8:EB:1B:6F:A7:5F",
                    right_address="E8:EB:1B:79:B1:AB",
                    address_type="public",
                    csv_path=None,
                    tare_duration=0,
                )
                self.events = []

            async def wait_official_gatt_settle(self, plate):
                self.events.append(f"settle:{plate.side}")

            async def discover_official_services(self, plate, fake_client):
                self.events.append(f"discover:{plate.side}")

        client = FakeClient()
        right = client.dual.plates[1]
        connection = FakeConnection()
        started_discoveries = {}

        asyncio.run(
            client.complete_initial_official_discovery(
                object(),
                right,
                object,
                connection,
                {right: connection.gatt_client},
                started_discoveries,
                15.0,
            )
        )

        self.assertEqual(
            client.events,
            ["settle:droite", "discover:droite"],
        )

    def test_bumble_preflight_rejects_left_initial_0x3e_not_seen_officially(self):
        class FakeConnection:
            def __init__(self, label):
                self.gatt_client = object()
                self.handle = 0x10 if label == "initial" else 0x11

            def on(self, event_name, callback):
                pass

        class FakeClient(KPlatesBumbleClient):
            def __init__(self):
                super().__init__(
                    transport="usb:0",
                    left_address="E8:EB:1B:6F:A7:5F",
                    right_address="E8:EB:1B:79:B1:AB",
                    address_type="public",
                    csv_path=None,
                    tare_duration=0,
                )
                self.discoveries = []
                self.reconnects = []
                self.reads = []
                self.settles = []

            async def wait_official_gatt_settle(self, plate):
                self.settles.append(plate.side)

            async def discover_official_services(self, plate, fake_client):
                self.discoveries.append(plate.side)
                if plate.side == "gauche" and self.discoveries.count("gauche") == 1:
                    self.disconnect_reasons[plate] = 0x3E
                    self.disconnected.add(plate)
                    plate.handle = None
                    raise asyncio.CancelledError()

            async def connect_plate(self, device, plate, Address, connect_timeout):
                self.reconnects.append(plate.side)
                plate.handle = 0x11
                self.disconnected.discard(plate)
                self.disconnect_reasons.pop(plate, None)
                return FakeConnection("reconnected")

            async def read_official_model(self, plate, fake_client):
                self.reads.append(plate.side)

        client = FakeClient()
        left, right = client.dual.plates
        left_connection = FakeConnection("initial")
        right_connection = FakeConnection("initial")
        client.connections = {
            right: right_connection,
            left: left_connection,
        }
        clients = {
            right: right_connection.gatt_client,
            left: left_connection.gatt_client,
        }

        with self.assertRaisesRegex(RuntimeError, "gauche.*capture officielle"):
            asyncio.run(
                client.run_official_gatt_preflight(
                    clients,
                    device=object(),
                    Address=object,
                    connect_timeout=15.0,
                )
            )

        self.assertEqual(
            client.discoveries,
            ["droite", "gauche"],
        )
        self.assertEqual(client.reconnects, [])
        self.assertEqual(client.settles, ["droite", "gauche"])
        self.assertIn(left, client.disconnected)
        self.assertEqual(client.reads, [])

    def test_bumble_kplates_defaults_match_validated_official_path(self):
        args = build_parser().parse_args([])

        self.assertEqual(args.connection_order, "right-first")
        self.assertEqual(args.gatt_preflight, "official-discovery")
        self.assertEqual(args.hold_after, 0.0)
        self.assertEqual(args.cycles, 1)
        self.assertEqual(args.rest_between_cycles, 0.0)


if __name__ == "__main__":
    unittest.main()
