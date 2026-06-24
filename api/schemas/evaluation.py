from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EvaluationBase(BaseModel):
    patient_id: int
    session_type: str = Field(default="evaluation")
    sensor: str = Field(..., min_length=1)
    test_name: str = Field(..., min_length=1)
    display_name: Optional[str] = None
    summary: Optional[str] = None
    csv_path: Optional[str] = None
    report_path: Optional[str] = None


class EvaluationCreate(EvaluationBase):
    pass


class EvaluationRead(EvaluationBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
