import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ble.kinvent.bluetooth_manager as manager
from ble.kinvent.bumble_backend import (
    BUMBLE_BACKEND,
    BumbleBackendError,
    normalize_backend,
)
from scripts.kinvent_bluetooth_manager import KinventBluetoothManager


class BluetoothManagerTest(unittest.TestCase):
    def test_requests_one_sensor_target(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            with mock.patch.object(manager, "CONTROL_PATH", control), \
                    mock.patch.object(manager, "RAW_DIR", Path(directory)):
                command = manager.request_sensor("kmove")

        self.assertEqual(command["action"], "select")
        self.assertEqual(command["target"], "kmove")

    def test_rejects_unknown_sensor(self):
        with self.assertRaises(ValueError):
            manager.request_sensor("inconnu")

    def test_manager_starts_bumble_without_raw_hci_controller(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.state = mock.Mock()
        with mock.patch(
            "scripts.kinvent_bluetooth_manager.require_bumble"
        ) as require_bumble:
            bluetooth.start()

        require_bumble.assert_called_once_with()
        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "idle")
        self.assertEqual(
            bluetooth.state.call_args.kwargs["backend"],
            BUMBLE_BACKEND,
        )

    def test_default_backend_is_bumble(self):
        bluetooth = KinventBluetoothManager()

        self.assertEqual(bluetooth.backend, BUMBLE_BACKEND)

    def test_rejects_unknown_bluetooth_backend(self):
        with self.assertRaises(BumbleBackendError):
            normalize_backend("fantaisie")

    def test_failed_bumble_child_requires_manual_reconnection(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.state = mock.Mock()

        recovered = bluetooth.recover_controller_after_failure("kplates", 1)

        self.assertFalse(recovered)
        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertIn("Pilote Bumble interrompu", bluetooth.state.call_args.kwargs["error"])

    def test_recovered_child_failure_returns_manager_to_idle(self):
        self.assertEqual(
            KinventBluetoothManager.child_exit_phase(1, recovered=True),
            "idle",
        )
        self.assertEqual(
            KinventBluetoothManager.child_exit_phase(1, recovered=False),
            "error",
        )

    def test_manager_ignores_non_global_control_actions(self):
        self.assertTrue(
            KinventBluetoothManager.is_manager_command(
                {"action": "select", "target": "kplates"}
            )
        )
        self.assertTrue(
            KinventBluetoothManager.is_manager_command(
                {"action": "disconnect", "target": None}
            )
        )
        self.assertFalse(
            KinventBluetoothManager.is_manager_command(
                {"action": "idle", "target": None}
            )
        )
        self.assertFalse(
            KinventBluetoothManager.is_manager_command(
                {"action": "start", "target": None}
            )
        )

    def test_manager_relaunches_kplates_when_worker_state_is_missing(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.target = "kplates"
        bluetooth.child = mock.Mock(pid=123)
        bluetooth.child.poll.return_value = None

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            self.assertFalse(bluetooth.current_target_is_active("kplates"))

    def test_manager_keeps_kplates_when_worker_state_matches_child(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.target = "kplates"
        bluetooth.child = mock.Mock(pid=123)
        bluetooth.child.poll.return_value = None

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            state = Path(directory) / "kplates_worker_state.json"
            state.write_text('{"pid":123,"phase":"idle"}', encoding="utf-8")

            self.assertTrue(bluetooth.current_target_is_active("kplates"))

    def test_manager_relaunches_kplates_when_worker_pid_is_stale(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.target = "kplates"
        bluetooth.child = mock.Mock(pid=123)
        bluetooth.child.poll.return_value = None

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            state = Path(directory) / "kplates_worker_state.json"
            state.write_text('{"pid":999,"phase":"idle"}', encoding="utf-8")

            self.assertFalse(bluetooth.current_target_is_active("kplates"))

    def test_stop_child_reports_usb_block_without_crashing(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.target = "kplates"
        bluetooth.generation = "request-1"
        bluetooth.state = mock.Mock()
        bluetooth.child = mock.Mock(pid=123)

        timeout = subprocess.TimeoutExpired(["worker"], 1)
        bluetooth.child.wait.side_effect = [timeout, timeout, timeout]

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            stopped = bluetooth.stop_child()

        self.assertFalse(stopped)
        bluetooth.child.terminate.assert_called_once_with()
        bluetooth.child.kill.assert_called_once_with()
        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertEqual(
            bluetooth.state.call_args.kwargs["blocked_child_pid"],
            123,
        )
        self.assertIn(
            "dongle nRF52840",
            bluetooth.state.call_args.kwargs["error"],
        )

    def test_process_waits_for_its_exact_manager_generation(self):
        process = manager.ManagedSensorProcess("kmove", "request-2")
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(
                '{"generation": "request-2", "target": "kmove"}',
                encoding="utf-8",
            )
            with mock.patch.object(manager, "CONTROL_PATH", control), \
                    mock.patch.object(
                        manager,
                        "manager_state",
                        return_value={
                            "pid": 123,
                            "generation": "request-1",
                            "target": None,
                            "phase": "idle",
                        },
                    ), mock.patch.object(manager.os, "kill"):
                self.assertIsNone(process.poll())

    def test_process_ignores_stale_generation_when_command_moved_on(self):
        process = manager.ManagedSensorProcess("kmove", "request-2")
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(
                '{"generation": "request-3", "target": null}',
                encoding="utf-8",
            )
            with mock.patch.object(manager, "CONTROL_PATH", control), \
                    mock.patch.object(
                        manager,
                        "manager_state",
                        return_value={
                            "pid": 123,
                            "generation": "request-3",
                            "target": None,
                            "phase": "idle",
                        },
                    ), mock.patch.object(manager.os, "kill"):
                self.assertEqual(process.poll(), 0)

    def test_process_stays_alive_for_matching_active_target(self):
        process = manager.ManagedSensorProcess("kmove", "request-2")
        with mock.patch.object(
            manager,
            "manager_state",
            return_value={
                "pid": 123,
                "generation": "request-2",
                "target": "kmove",
                "phase": "active",
            },
        ), mock.patch.object(manager.os, "kill"):
            self.assertIsNone(process.poll())

    def test_root_manager_permission_error_still_means_alive(self):
        process = manager.ManagedSensorProcess("kmove", "request-2")
        with mock.patch.object(
            manager,
            "manager_state",
            return_value={
                "pid": 123,
                "generation": "request-2",
                "target": "kmove",
                "phase": "active",
            },
        ), mock.patch.object(
            manager.os,
            "kill",
            side_effect=PermissionError,
        ):
            self.assertIsNone(process.poll())
