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
                "backend": "bumble",
                "transport": "usb:0",
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
        ):
            snapshot = devices_module.devices_snapshot()

        self.assertEqual(snapshot["manager"]["target"], "kplates")
        self.assertEqual(
            [device["key"] for device in snapshot["devices"]],
            ["kplates", "kpush", "kpull", "kmove"],
        )
        kplates = snapshot["devices"][0]
        self.assertTrue(kplates["connected"])
        self.assertTrue(kplates["active"])
        self.assertEqual(len(kplates["addresses"]), 2)


if __name__ == "__main__":
    unittest.main()
