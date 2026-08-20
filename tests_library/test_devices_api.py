import unittest
import importlib.util
from unittest import mock


class DevicesApiTest(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_devices_snapshot_lists_known_kinvent_devices(self):
        import api.routes.devices as devices_module

        with mock.patch.object(
            devices_module,
            "manager_state",
            return_value={
                "phase": "active",
                "target": "kplates",
                "backend": "hci-direct",
                "hci_adapter": "hci0",
            },
        ), mock.patch.object(
            devices_module.dual_plate_service,
            "status",
            return_value={
                "worker_phase": "idle",
                "bluetooth_connected": True,
                "connected_sides": ["gauche", "droite"],
            },
        ), mock.patch.object(
            devices_module.kpush_service,
            "status",
            return_value={"phase": "disconnected", "connected": False},
        ), mock.patch.object(
            devices_module.kpull_service,
            "status",
            return_value={"phase": "disconnected", "connected": False},
        ), mock.patch.object(
            devices_module.kmove_service,
            "status",
            return_value={"phase": "disconnected", "connected": False},
        ), mock.patch.object(
            devices_module.anr_m40_service,
            "status",
            return_value={"phase": "disconnected", "connected": False},
        ):
            snapshot = devices_module.devices_snapshot()

        self.assertEqual(snapshot["manager"]["target"], "kplates")
        self.assertEqual(
            [device["key"] for device in snapshot["devices"]],
            ["kplates", "kpush", "kpull", "kmove", "anr_m40"],
        )
        kplates = snapshot["devices"][0]
        self.assertTrue(kplates["connected"])
        self.assertTrue(kplates["active"])
        self.assertEqual(len(kplates["addresses"]), 2)

    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_connect_device_uses_existing_sensor_services(self):
        import api.routes.devices as devices_module

        with mock.patch.object(
            devices_module.dual_plate_service,
            "connect",
            return_value={"worker_phase": "idle"},
        ) as connect_kplates, mock.patch.object(
            devices_module,
            "devices_snapshot",
            return_value={"devices": []},
        ):
            self.assertEqual(
                devices_module.connect_device("kplates"),
                {"devices": []},
            )

        connect_kplates.assert_called_once_with()

    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_unknown_device_connection_is_rejected(self):
        from fastapi import HTTPException
        import api.routes.devices as devices_module

        with self.assertRaises(HTTPException) as context:
            devices_module.connect_device("unknown")

        self.assertEqual(context.exception.status_code, 404)

    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_disconnect_device_uses_existing_sensor_services(self):
        import api.routes.devices as devices_module

        with mock.patch.object(
            devices_module.kpush_service,
            "disconnect",
            return_value={"phase": "disconnected"},
        ) as disconnect_kpush, mock.patch.object(
            devices_module,
            "devices_snapshot",
            return_value={"devices": []},
        ):
            self.assertEqual(
                devices_module.disconnect_device("kpush"),
                {"devices": []},
            )

        disconnect_kpush.assert_called_once_with()

    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_connect_device_supports_anr_m40(self):
        import api.routes.devices as devices_module

        with mock.patch.object(
            devices_module.anr_m40_service,
            "connect",
            return_value={"phase": "ready"},
        ) as connect_anr, mock.patch.object(
            devices_module,
            "devices_snapshot",
            return_value={"devices": []},
        ):
            self.assertEqual(
                devices_module.connect_device("anr_m40"),
                {"devices": []},
            )

        connect_anr.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
