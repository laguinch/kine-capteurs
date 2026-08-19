from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ble.anr.acquisition_service import anr_m40_service


router = APIRouter(prefix="/api/anr-m40", tags=["ANR M40"])


class ANRM40Request(BaseModel):
    duration: float = Field(default=30.0, ge=5.0, le=300.0)
    filename: str | None = None


@router.post("/connect")
def connect_anr_m40():
    return anr_m40_service.connect()


@router.post("/disconnect")
def disconnect_anr_m40():
    try:
        return anr_m40_service.disconnect()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/start")
def start_anr_m40(request: ANRM40Request):
    try:
        return anr_m40_service.start(
            duration=request.duration,
            filename=request.filename,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop_anr_m40():
    return anr_m40_service.stop()


@router.get("/latest")
def latest_anr_m40():
    return anr_m40_service.latest()


@router.get("/download")
def download_anr_m40():
    status = anr_m40_service.status()
    csv_path = status.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Aucun fichier disponible.")
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=Path(csv_path).name,
    )
