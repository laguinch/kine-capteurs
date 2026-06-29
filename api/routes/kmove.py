from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ble.kinvent.kmove.acquisition_service import kmove_service


router = APIRouter(prefix="/api/kmove", tags=["K-Move"])


class KMoveRequest(BaseModel):
    duration: float | None = Field(default=30.0, ge=5.0, le=300.0)
    filename: str | None = None


class KMoveConnectRequest(BaseModel):
    reference_duration: float = Field(default=2.0, ge=0.5, le=10.0)


@router.post("/connect")
def connect_kmove(request: KMoveConnectRequest):
    return kmove_service.connect(
        reference_duration=request.reference_duration,
    )


@router.post("/disconnect")
def disconnect_kmove():
    try:
        return kmove_service.disconnect()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/start")
def start_kmove(request: KMoveRequest):
    try:
        return kmove_service.start(
            duration=request.duration,
            filename=request.filename,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop_kmove():
    return kmove_service.stop()


@router.get("/latest")
def latest_kmove():
    return kmove_service.latest()


@router.get("/download")
def download_kmove():
    status = kmove_service.status()
    csv_path = status.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Aucun fichier disponible.")
    if status.get("running") or status.get("phase") in {"active", "armed", "reference"}:
        raise HTTPException(
            status_code=409,
            detail="Arrêtez le test avant de télécharger le fichier.",
        )
    if not status.get("finished_at"):
        raise HTTPException(
            status_code=409,
            detail="Aucun test terminé disponible au téléchargement.",
        )
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=Path(csv_path).name,
    )
