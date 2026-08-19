import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import ble.kinvent.bluetooth_manager as manager
from ble.kinvent.bumble_backend import (
    BUMBLE_BACKEND,
    BumbleBackendError,
    normalize_backend,
)
from scripts.kinvent_bluetooth_manager import (
    KPLATES_BACKEND_BUMBLE,
    KPLATES_BACKEND_HCI,
    KinventBluetoothManager,
)


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
        self.assertEqual(
            bluetooth.state.call_args.kwargs["kplates_backend"],
            KPLATES_BACKEND_BUMBLE,
        )
        self.assertEqual(
            bluetooth.state.call_args.kwargs["hci_adapter"],
            "hci0",
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

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            recovered = bluetooth.recover_controller_after_failure("kplates", 1)

        self.assertFalse(recovered)
        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertIn(
            "Pilote Bluetooth interrompu",
            bluetooth.state.call_args.kwargs["error"],
        )

    def test_failed_bumble_child_reports_worker_error(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.state = mock.Mock()

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            state = Path(directory) / "kplates_worker_state.json"
            state.write_text(
                (
                    '{"phase":"error",'
                    '"error":"Dongle Bumble indisponible: device not found"}'
                ),
                encoding="utf-8",
            )

            recovered = bluetooth.recover_controller_after_failure("kplates", 1)

        self.assertFalse(recovered)
        self.assertEqual(bluetooth.state.call_args.args[0], "error")
        self.assertEqual(
            bluetooth.state.call_args.kwargs["error"],
            "Dongle Bumble indisponible: device not found",
        )

    def test_kplates_default_launch_uses_bumble_on_nrf(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory) / "raw"
            base_dir = Path(directory) / "project"
            raw_dir.mkdir()
            base_dir.mkdir()
            bluetooth = KinventBluetoothManager()
            bluetooth.state = mock.Mock()

            with mock.patch(
                "scripts.kinvent_bluetooth_manager.RAW_DIR",
                raw_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.BASE_DIR",
                base_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4321
                bluetooth.launch("kplates")

        command = popen.call_args.args[0]
        self.assertIn("kinvent_kplates_bumble.py", command[2])
        self.assertIn("--transport", command)
        self.assertEqual(command[command.index("--transport") + 1], "usb:0")
        self.assertNotIn("--adapter", command)
        bluetooth.state.assert_called_once_with("active")

    def test_kplates_hci_backend_can_still_be_selected_explicitly(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            "os.environ",
            {"KINE_KPLATES_BACKEND": KPLATES_BACKEND_HCI, "KINE_HCI_ADAPTER": "hci1"},
            clear=False,
        ):
            raw_dir = Path(directory) / "raw"
            base_dir = Path(directory) / "project"
            raw_dir.mkdir()
            base_dir.mkdir()
            bluetooth = KinventBluetoothManager()
            bluetooth.state = mock.Mock()

            with mock.patch(
                "scripts.kinvent_bluetooth_manager.RAW_DIR",
                raw_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.BASE_DIR",
                base_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4321
                bluetooth.launch("kplates")

        command = popen.call_args.args[0]
        self.assertIn("kinvent_dual_hci.py", command[2])
        self.assertEqual(command[command.index("--adapter") + 1], "hci1")

    def test_anr_m40_launch_uses_bumble_transport(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            "os.environ",
            {"KINE_BUMBLE_TRANSPORT": "usb:0"},
            clear=False,
        ):
            raw_dir = Path(directory) / "raw"
            base_dir = Path(directory) / "project"
            raw_dir.mkdir()
            base_dir.mkdir()
            bluetooth = KinventBluetoothManager()
            bluetooth.state = mock.Mock()

            with mock.patch(
                "scripts.kinvent_bluetooth_manager.RAW_DIR",
                raw_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.BASE_DIR",
                base_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4321
                bluetooth.launch("anr_m40")

        command = popen.call_args.args[0]
        self.assertIn("anr_m40_bumble.py", command[2])
        self.assertIn("--transport", command)
        self.assertEqual(command[command.index("--transport") + 1], "usb:0")
        self.assertIn("--control-file", command)
        self.assertNotIn("--adapter", command)
        self.assertNotIn("--skip-mtu", command)

    def test_kplates_bumble_backend_remains_available_for_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            "os.environ",
            {
                "KINE_KPLATES_BACKEND": KPLATES_BACKEND_BUMBLE,
                "KINE_BUMBLE_TRANSPORT": "usb:0",
            },
            clear=False,
        ):
            raw_dir = Path(directory) / "raw"
            base_dir = Path(directory) / "project"
            raw_dir.mkdir()
            base_dir.mkdir()
            bluetooth = KinventBluetoothManager()
            bluetooth.state = mock.Mock()

            with mock.patch(
                "scripts.kinvent_bluetooth_manager.RAW_DIR",
                raw_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.BASE_DIR",
                base_dir,
            ), mock.patch(
                "scripts.kinvent_bluetooth_manager.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4321
                bluetooth.launch("kplates")

        command = popen.call_args.args[0]
        self.assertIn("kinvent_kplates_bumble.py", command[2])
        self.assertIn("--transport", command)
        self.assertEqual(command[command.index("--transport") + 1], "usb:0")

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
        updated_at = datetime.now(timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            state = Path(directory) / "kplates_worker_state.json"
            state.write_text(
                (
                    '{"pid":123,"phase":"idle",'
                    f'"updated_at":"{updated_at}"'
                    "}"
                ),
                encoding="utf-8",
            )

            self.assertTrue(bluetooth.current_target_is_active("kplates"))

    def test_manager_relaunches_kplates_when_worker_state_is_stale(self):
        bluetooth = KinventBluetoothManager()
        bluetooth.target = "kplates"
        bluetooth.state = mock.Mock()
        bluetooth.child = mock.Mock(pid=123)
        bluetooth.child.poll.return_value = None
        updated_at = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        ).isoformat()

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "scripts.kinvent_bluetooth_manager.RAW_DIR",
            Path(directory),
        ):
            state = Path(directory) / "kplates_worker_state.json"
            state.write_text(
                (
                    '{"pid":123,"phase":"idle",'
                    f'"updated_at":"{updated_at}"'
                    "}"
                ),
                encoding="utf-8",
            )

            self.assertFalse(bluetooth.current_target_is_active("kplates"))

        bluetooth.state.assert_called_once()
        self.assertEqual(bluetooth.state.call_args.args[0], "switching")
        self.assertIn(
            "périmé",
            bluetooth.state.call_args.kwargs["error"],
        )

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
