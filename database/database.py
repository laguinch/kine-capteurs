from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import STORAGE_DIR

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = STORAGE_DIR / "kine.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import database.models.patient  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_patient_columns()


def _ensure_patient_columns():
    inspector = inspect(engine)
    if "patients" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("patients")}
    statements = []
    if "created_at" not in columns:
        statements.append("ALTER TABLE patients ADD COLUMN created_at DATETIME")
    if "updated_at" not in columns:
        statements.append("ALTER TABLE patients ADD COLUMN updated_at DATETIME")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                "UPDATE patients "
                "SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP), "
                "updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)"
            )
        )
