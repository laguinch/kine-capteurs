import importlib.util
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    importlib.util.find_spec("fastapi"),
    "FastAPI est installé dans l'environnement serveur.",
)
class EvaluationApiTest(unittest.TestCase):
    def test_create_and_list_patient_evaluations(self):
        import api.routes.evaluations as evaluations_routes
        import api.routes.patients as patients_routes
        import database.database as database_module
        from database.database import Base

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluations.db"
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
                    patients_routes.PatientCreate(nom="Martin", prenom="Paul"),
                    db=db,
                )
                evaluation = evaluations_routes.create_evaluation(
                    evaluations_routes.EvaluationCreate(
                        patient_id=patient.id,
                        session_type="evaluation",
                        sensor="K-Force Plates",
                        test_name="CMJ",
                        display_name="CMJ — saut vertical",
                        summary="Hauteur 15 cm",
                        csv_path="/tmp/cmj.csv",
                    ),
                    db=db,
                )
                duplicate = evaluations_routes.create_evaluation(
                    evaluations_routes.EvaluationCreate(
                        patient_id=patient.id,
                        session_type="evaluation",
                        sensor="K-Force Plates",
                        test_name="CMJ",
                        display_name="CMJ — saut vertical",
                        summary="Hauteur 15 cm",
                        csv_path="/tmp/cmj.csv",
                    ),
                    db=db,
                )

                self.assertEqual(evaluation.patient_id, patient.id)
                self.assertEqual(duplicate.id, evaluation.id)
                results = evaluations_routes.list_patient_evaluations(patient.id, db=db)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].test_name, "CMJ")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
