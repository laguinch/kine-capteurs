import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ble.kinvent.bluetooth_manager as manager
from ble.kinvent.bumble_backend import (
    BUMBLE_BACKEND,
    RAW_HCI_BACKEND,
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

    def test_controller_is_reset_only_when_manager_starts(self):
        bluetooth = KinventBluetoothManager(0)
        bluetooth.controller.open = mock.Mock()
        bluetooth.controller.reset = mock.Mock()
        bluetooth.state = mock.Mock()

        bluetooth.start()

        bluetooth.controller.open.assert_called_once_with()
        bluetooth.controller.reset.assert_called_once_with()
        bluetooth.state.assert_called_once_with("idle")

    def test_default_backend_remains_raw_hci(self):
        bluetooth = KinventBluetoothManager(0)

        self.assertEqual(bluetooth.backend, RAW_HCI_BACKEND)

    def test_rejects_unknown_bluetooth_backend(self):
        with self.assertRaises(BumbleBackendError):
            normalize_backend("fantaisie")

    def test_bumble_backend_is_explicitly_guarded_before_production_switch(self):
        bluetooth = KinventBluetoothManager(0, backend=BUMBLE_BACKEND)
        bluetooth.state = mock.Mock()
        with mock.patch(
            "scripts.kinvent_bluetooth_manager.require_bumble"
        ) as require_bumble:
            with self.assertRaises(RuntimeError):
                bluetooth.start()

        require_bumble.assert_called_once_with()
        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertEqual(
            bluetooth.state.call_args.kwargs["backend"],
            BUMBLE_BACKEND,
        )

    def test_start_reports_bluetooth_controller_error(self):
        bluetooth = KinventBluetoothManager(0)
        bluetooth.controller.open = mock.Mock()
        bluetooth.controller.reset = mock.Mock(
            side_effect=TimeoutError("Pas de réponse HCI")
        )
        bluetooth.controller.close = mock.Mock()
        bluetooth.state = mock.Mock()

        with self.assertRaises(TimeoutError):
            bluetooth.start()

        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertIn(
            "Contrôleur Bluetooth indisponible",
            bluetooth.state.call_args.kwargs["error"],
        )
        bluetooth.controller.close.assert_called_once_with()

    def test_recovers_controller_after_failed_child(self):
        bluetooth = KinventBluetoothManager(0)
        bluetooth.controller.sock = object()
        bluetooth.controller.reset = mock.Mock(
            side_effect=[TimeoutError("reset muet"), None]
        )
        bluetooth.controller.close = mock.Mock()
        bluetooth.controller.open = mock.Mock()
        bluetooth.state = mock.Mock()

        recovered = bluetooth.recover_controller_after_failure("kplates", 1)

        self.assertTrue(recovered)
        self.assertEqual(bluetooth.controller.reset.call_count, 2)
        bluetooth.controller.close.assert_called_once_with()
        bluetooth.controller.open.assert_called_once_with()

    def test_recovery_reopens_closed_controller_before_reset(self):
        bluetooth = KinventBluetoothManager(0)
        bluetooth.controller.sock = None

        def reopen():
            bluetooth.controller.sock = object()

        bluetooth.controller.open = mock.Mock(side_effect=reopen)
        bluetooth.controller.reset = mock.Mock()
        bluetooth.state = mock.Mock()

        recovered = bluetooth.recover_controller_after_failure("kplates", 1)

        self.assertTrue(recovered)
        bluetooth.controller.open.assert_called_once_with()
        bluetooth.controller.reset.assert_called_once_with()

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
