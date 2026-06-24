import importlib.util
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    importlib.util.find_spec("fastapi"),
    "FastAPI est installé dans l'environnement serveur.",
)
class PatientApiTest(unittest.TestCase):
    def test_create_search_update_and_delete_patient(self):
        import api.routes.patients as patients_routes
        import database.database as database_module
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
            db = TestingSessionLocal()
            try:
                patient = patients_routes.create_patient(
                    patients_routes.PatientCreate(
                        nom="Dupont",
                        prenom="Marie",
                        date_naissance="1980-04-12",
                        pathologie="Genou",
                    ),
                    db=db,
                )

                self.assertEqual(patient.nom, "Dupont")
                self.assertEqual(patient.prenom, "Marie")

                results = patients_routes.list_patients(q="genou", db=db)
                self.assertEqual(len(results), 1)

                loaded = patients_routes.get_patient(patient.id, db=db)
                self.assertEqual(str(loaded.date_naissance), "1980-04-12")

                updated = patients_routes.update_patient(
                    patient.id,
                    patients_routes.PatientUpdate(pathologie="Épaule"),
                    db=db,
                )
                self.assertEqual(updated.pathologie, "Épaule")

                patients_routes.delete_patient(patient.id, db=db)
                self.assertEqual(patients_routes.list_patients(db=db), [])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
