from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from database.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    session_type = Column(String, nullable=False, default="evaluation")
    sensor = Column(String, nullable=False)
    test_name = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    csv_path = Column(String, nullable=True)
    report_path = Column(String, nullable=True)
