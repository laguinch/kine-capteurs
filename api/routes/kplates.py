from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ble.kinvent.kplates.acquisition_service import dual_plate_service
from ble.kinvent.kplates.cmj_analysis import analyze_cmj_csv


router = APIRouter(prefix="/api/kplates", tags=["K-Force Plates"])


class DualAcquisitionRequest(BaseModel):
    adapter: str = "hci1"
    duration: float = Field(default=30.0, ge=5.0, le=600.0)
    tare_duration: float = Field(default=2.0, ge=0.5, le=10.0)
    sync_tolerance_ms: float = Field(default=20.0, ge=5.0, le=100.0)
    filename: str | None = None
    recalibrate: bool = False
    mode: str = "balance"


@router.post("/dual/start")
def start_dual_acquisition(request: DualAcquisitionRequest):
    try:
        return dual_plate_service.start(
            adapter=request.adapter,
            duration=request.duration,
            tare_duration=request.tare_duration,
            sync_tolerance_ms=request.sync_tolerance_ms,
            filename=request.filename,
            recalibrate=request.recalibrate,
            mode=request.mode,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dual/stop")
def stop_dual_acquisition():
    return dual_plate_service.stop()


@router.post("/dual/connect")
def connect_dual_plates():
    try:
        return dual_plate_service.connect()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dual/disconnect")
def disconnect_dual_plates():
    try:
        return dual_plate_service.disconnect()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/dual/status")
def dual_acquisition_status():
    return dual_plate_service.status()


@router.get("/dual/latest")
def dual_acquisition_latest():
    return dual_plate_service.latest()


@router.get("/dual/download")
def download_dual_acquisition():
    status = dual_plate_service.status()
    csv_path = status.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Aucun fichier disponible.")
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=Path(csv_path).name,
    )


@router.get("/cmj/result")
def cmj_result():
    status = dual_plate_service.status()
    if status.get("running"):
        raise HTTPException(
            status_code=409,
            detail="L'analyse CMJ sera disponible après la fin du test.",
        )
    csv_path = status.get("csv_path")
    if status.get("mode") != "cmj" or not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Aucun test CMJ disponible.")
    try:
        return analyze_cmj_csv(csv_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
