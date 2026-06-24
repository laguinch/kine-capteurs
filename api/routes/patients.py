from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.schemas.patient import PatientCreate, PatientRead, PatientUpdate
from database.database import get_db
from database.models.patient import Patient

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _apply_payload(patient: Patient, payload):
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(exclude_unset=True)
    else:
        data = payload.dict(exclude_unset=True)
    for key, value in data.items():
        if key in {"nom", "prenom", "pathologie", "commentaires"}:
            value = _normalize(value)
        setattr(patient, key, value)


@router.get("", response_model=list[PatientRead])
def list_patients(
    q: str | None = Query(default=None, description="Nom, prénom ou pathologie"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Patient)
    search = _normalize(q)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                Patient.nom.ilike(pattern),
                Patient.prenom.ilike(pattern),
                Patient.pathologie.ilike(pattern),
            )
        )
    return query.order_by(Patient.nom.asc(), Patient.prenom.asc()).limit(limit).all()


@router.post("", response_model=PatientRead, status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = Patient()
    _apply_payload(patient, payload)
    if not patient.nom or not patient.prenom:
        raise HTTPException(status_code=422, detail="Nom et prénom sont obligatoires.")
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientRead)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    return patient


@router.put("/{patient_id}", response_model=PatientRead)
def update_patient(patient_id: int, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    _apply_payload(patient, payload)
    if not patient.nom or not patient.prenom:
        raise HTTPException(status_code=422, detail="Nom et prénom sont obligatoires.")
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    db.delete(patient)
    db.commit()
