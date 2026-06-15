from sqlalchemy import Column, Integer, String, Date, Text
from database.database import Base

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String, nullable=False)
    prenom = Column(String, nullable=False)
    date_naissance = Column(Date, nullable=True)
    date_ordonnance = Column(Date, nullable=True)
    pathologie = Column(String, nullable=True)
    commentaires = Column(Text, nullable=True)
