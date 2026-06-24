from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class PatientBase(BaseModel):
    nom: str = Field(..., min_length=1)
    prenom: str = Field(..., min_length=1)
    date_naissance: Optional[date] = None
    date_ordonnance: Optional[date] = None
    pathologie: Optional[str] = None
    commentaires: Optional[str] = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    nom: Optional[str] = Field(default=None, min_length=1)
    prenom: Optional[str] = Field(default=None, min_length=1)
    date_naissance: Optional[date] = None
    date_ordonnance: Optional[date] = None
    pathologie: Optional[str] = None
    commentaires: Optional[str] = None


class PatientRead(PatientBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
        from_attributes = True
