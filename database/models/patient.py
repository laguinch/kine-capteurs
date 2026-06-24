from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Integer, String, Text

from database.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    nom = Column(String, nullable=False)
    prenom = Column(String, nullable=False)
    date_naissance = Column(Date, nullable=True)
    date_ordonnance = Column(Date, nullable=True)
    pathologie = Column(String, nullable=True)
    commentaires = Column(Text, nullable=True)
