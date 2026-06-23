import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ble.kinvent.bluetooth_manager as manager
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

    def test_process_waits_for_its_exact_manager_generation(self):
        process = manager.ManagedSensorProcess("kmove", "request-2")
        with mock.patch.object(
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
