import importlib.util
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    importlib.util.find_spec("fastapi"),
    "FastAPI est installé dans l'environnement serveur.",
)
class PatientApiTest(unittest.TestCase):
    def test_create_search_and_get_patient(self):
        from fastapi.testclient import TestClient

        import database.database as database_module
        from app.main import app
        from database.database import Base

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patients.db"
            engine = database_module.create_engine(
                f"sqlite:///{path}",
                connect_args={"check_same_thread": False},
            )
            TestingSessionLocal = database_module.sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
            )
            Base.metadata.create_all(bind=engine)

            def override_get_db():
                db = TestingSessionLocal()
                try:
                    yield db
                finally:
                    db.close()

            app.dependency_overrides[database_module.get_db] = override_get_db
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/patients",
                    json={
                        "nom": "Dupont",
                        "prenom": "Marie",
                        "date_naissance": "1980-04-12",
                        "pathologie": "Genou",
                    },
                )
                self.assertEqual(response.status_code, 201)
                patient = response.json()
                self.assertEqual(patient["nom"], "Dupont")
                self.assertEqual(patient["prenom"], "Marie")

                response = client.get("/api/patients?q=genou")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()), 1)

                response = client.get(f"/api/patients/{patient['id']}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["date_naissance"], "1980-04-12")
            finally:
                app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
