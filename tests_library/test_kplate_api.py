import importlib.util
import unittest

from ble.kinvent.kplates.acquisition_service import DualPlateAcquisitionService


class KPlateApiTest(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("fastapi"),
        "FastAPI est installé dans l'environnement serveur.",
    )
    def test_routes_are_registered(self):
        from app.main import app

        paths = {route.path for route in app.routes}

        self.assertIn("/api/kplates/dual/start", paths)
        self.assertIn("/api/kplates/dual/stop", paths)
        self.assertIn("/api/kplates/dual/status", paths)

    def test_new_service_is_idle(self):
        status = DualPlateAcquisitionService().status()

        self.assertFalse(status["running"])
        self.assertIsNone(status["pid"])
        self.assertIsNone(status["csv_path"])


if __name__ == "__main__":
    unittest.main()
