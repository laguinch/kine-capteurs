from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ble.kinvent.kpush.acquisition_service import kpush_service


router = APIRouter(prefix="/api/kpush", tags=["K-Push"])


class KPushRequest(BaseModel):
    duration: float = Field(default=30.0, ge=5.0, le=300.0)
    tare_duration: float = Field(default=2.0, ge=0.5, le=10.0)
    filename: str | None = None


@router.post("/start")
def start_kpush(request: KPushRequest):
    try:
        return kpush_service.start(
            duration=request.duration,
            filename=request.filename,
            tare_duration=request.tare_duration,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop_kpush():
    return kpush_service.stop()


@router.get("/latest")
def latest_kpush():
    return kpush_service.latest()


@router.get("/download")
def download_kpush():
    status = kpush_service.status()
    csv_path = status.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Aucun fichier disponible.")
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=Path(csv_path).name,
    )
