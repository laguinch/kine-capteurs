import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ble.kinvent.bluetooth_manager as shared
import scripts.kinvent_bluetooth_manager as runtime


class BluetoothResetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reset = self.root / 'reset.json'
        self.control = self.root / 'control.json'
        for module, name, value in (
            (runtime, 'RESET_PATH', self.reset),
            (shared, 'RESET_PATH', self.reset),
            (shared, 'CONTROL_PATH', self.control),
            (shared, 'RAW_DIR', self.root),
        ):
            patch = mock.patch.object(module, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.manager = runtime.KinventBluetoothManager()
        self.manager.state = mock.Mock()
        self.manager.stop_child = mock.Mock(return_value=True)
        self.manager.reset_controller = mock.Mock()
        self.command = {'generation': 'reset-1', 'action': 'reset'}

    def result(self):
        return json.loads(self.reset.read_text())

    def test_manual_reset_stops_child_before_controller_and_never_reconnects(self):
        calls = mock.Mock()
        calls.attach_mock(self.manager.stop_child, 'stop')
        calls.attach_mock(self.manager.reset_controller, 'reset')
        self.manager.launch = mock.Mock()
        self.manager.handle_reset(self.command)
        self.assertEqual(calls.mock_calls, [mock.call.stop(), mock.call.reset()])
        self.manager.launch.assert_not_called()
        self.assertEqual(self.result()['status'], 'done')

    def test_daily_reset_skips_connected_sensor(self):
        self.manager.target = 'kpush'
        self.manager.handle_reset({**self.command, 'idle_only': True})
        self.manager.stop_child.assert_not_called()
        self.manager.reset_controller.assert_not_called()
        self.assertEqual(self.result()['status'], 'skipped')

    def test_daily_reset_runs_when_no_sensor_is_owned(self):
        self.manager.handle_reset({**self.command, 'idle_only': True})
        self.manager.reset_controller.assert_called_once()
        self.assertEqual(self.result()['status'], 'done')

    def test_blocked_child_is_never_reset(self):
        self.manager.stop_child.return_value = False
        self.manager.handle_reset(self.command)
        self.manager.reset_controller.assert_not_called()
        self.assertEqual(self.result()['status'], 'error')

    def test_hci_failure_is_reported(self):
        self.manager.reset_controller.side_effect = TimeoutError('Pas de réponse HCI')
        self.manager.handle_reset(self.command)
        self.assertEqual(self.result()['status'], 'error')
        self.assertIn('Pas de réponse HCI', self.result()['message'])

    def test_only_controller_reset_is_sent_and_socket_is_closed(self):
        manager = runtime.KinventBluetoothManager()
        with mock.patch('scripts.kinvent_raw_hci.RawKinventClient') as client:
            client.return_value.send_command.return_value = 0x0c03
            manager.reset_controller()
            self.assertEqual(client.return_value.mock_calls, [
                mock.call.open(), mock.call.send_command(3, 3),
                mock.call.wait_for_command(0x0c03, 3.0), mock.call.close(),
            ])

    def test_reset_command_blocks_start_until_matching_completion(self):
        self.control.write_text(json.dumps(self.command))
        with self.assertRaises(RuntimeError):
            shared.ensure_no_reset()
        self.reset.write_text(json.dumps({'generation': 'old', 'status': 'done'}))
        with self.assertRaises(RuntimeError):
            shared.ensure_no_reset()
        self.reset.write_text(json.dumps({'generation': 'reset-1', 'status': 'done'}))
        shared.ensure_no_reset()

    def test_each_acquisition_start_rejects_pending_reset(self):
        from ble.kinvent.kpush.acquisition_service import kpush_service
        from ble.kinvent.kpull.acquisition_service import kpull_service
        from ble.kinvent.kmove.acquisition_service import kmove_service
        from ble.kinvent.kplates.acquisition_service import dual_plate_service
        from ble.anr.acquisition_service import anr_m40_service
        self.control.write_text(json.dumps(self.command))
        for service in (kpush_service, kpull_service, kmove_service, dual_plate_service, anr_m40_service):
            with self.subTest(service=type(service).__name__), self.assertRaisesRegex(RuntimeError, 'réinitialisation'):
                service.start()

    def test_missing_manager_is_rejected(self):
        with mock.patch.object(shared, 'manager_state', return_value={}):
            with self.assertRaises(RuntimeError):
                shared.request_reset()
        self.assertFalse(self.control.exists())


class BluetoothResetApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        import api.routes.devices as devices
        self.devices = devices
        app = FastAPI()
        app.include_router(devices.router)
        self.snapshot = {'manager': {'phase': 'idle'}, 'devices': [
            {'running': False, 'connected': False, 'phase': 'disconnected'}]}
        patch = mock.patch.object(devices, 'devices_snapshot', return_value=self.snapshot)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(devices, 'request_reset', return_value={'generation': 'test'})
        self.queue = patch.start()
        self.addCleanup(patch.stop)

    def post(self, payload):
        from fastapi import HTTPException
        from pydantic import ValidationError
        try:
            result = self.devices.reset_bluetooth(self.devices.BluetoothResetRequest(**payload))
            return 202, result
        except HTTPException as exc:
            return exc.status_code, None
        except ValidationError:
            return 422, None

    def test_confirmation_is_required(self):
        for payload in ({}, {'confirmed': False}):
            self.assertEqual(self.post(payload)[0], 422)
        self.queue.assert_not_called()

    def test_active_and_armed_tests_are_rejected(self):
        for phase in ('active', 'armed'):
            self.snapshot['devices'][0]['phase'] = phase
            self.assertEqual(self.post({'confirmed': True})[0], 409)
        self.queue.assert_not_called()

    def test_manual_reset_is_queued_not_reported_completed(self):
        status, result = self.post({'confirmed': True})
        self.assertEqual(status, 202)
        self.assertEqual(result, {'generation': 'test', 'status': 'queued'})

    def test_daily_reset_rejects_connection(self):
        self.snapshot['devices'][0]['connected'] = True
        self.assertEqual(self.post({'confirmed': True, 'idle_only': True})[0], 409)
        self.queue.assert_not_called()

    def test_manager_offline_is_an_error(self):
        self.queue.side_effect = RuntimeError('Gestionnaire arrêté')
        self.assertEqual(self.post({'confirmed': True})[0], 503)


class DailyResetTest(unittest.TestCase):
    def test_busy_server_skips_without_polling(self):
        from urllib.error import HTTPError
        from scripts import bluetooth_daily_reset as daily
        with mock.patch.object(daily, 'urlopen', side_effect=HTTPError(daily.URL, 409, 'busy', {}, None)) as call:
            daily.main()
        self.assertEqual(call.call_count, 1)
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload, {'confirmed': True, 'idle_only': True})

    def test_waits_for_matching_reset_completion(self):
        from io import BytesIO
        from scripts import bluetooth_daily_reset as daily
        replies = [BytesIO(b'{"generation":"r1"}'), BytesIO(b'{"generation":"r1","status":"done","message":"OK"}')]
        with mock.patch.object(daily, 'urlopen', side_effect=replies) as call:
            daily.main()
        self.assertEqual(call.call_count, 2)


if __name__ == '__main__':
    unittest.main()
