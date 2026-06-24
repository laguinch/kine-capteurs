from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.schemas.evaluation import EvaluationCreate, EvaluationRead
from database.database import get_db
from database.models.evaluation import Evaluation
from database.models.patient import Patient

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@router.get("/patient/{patient_id}", response_model=list[EvaluationRead])
def list_patient_evaluations(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    return (
        db.query(Evaluation)
        .filter(Evaluation.patient_id == patient_id)
        .order_by(Evaluation.created_at.desc())
        .all()
    )


@router.post("", response_model=EvaluationRead, status_code=201)
def create_evaluation(payload: EvaluationCreate, db: Session = Depends(get_db)):
    patient = db.get(Patient, payload.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")

    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    for key in ("session_type", "sensor", "test_name", "display_name", "summary", "csv_path", "report_path"):
        data[key] = _clean(data.get(key))
    if not data["session_type"]:
        data["session_type"] = "evaluation"
    if data.get("csv_path"):
        existing = (
            db.query(Evaluation)
            .filter(
                Evaluation.patient_id == payload.patient_id,
                Evaluation.csv_path == data["csv_path"],
            )
            .first()
        )
        if existing is not None:
            return existing
    evaluation = Evaluation(**data)
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return evaluation
