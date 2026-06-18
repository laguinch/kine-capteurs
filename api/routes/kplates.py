from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ble.kinvent.kplates.acquisition_service import dual_plate_service


router = APIRouter(prefix="/api/kplates", tags=["K-Force Plates"])


class DualAcquisitionRequest(BaseModel):
    adapter: str = "hci1"
    duration: float = Field(default=30.0, ge=5.0, le=600.0)
    tare_duration: float = Field(default=2.0, ge=0.5, le=10.0)
    sync_tolerance_ms: float = Field(default=20.0, ge=5.0, le=100.0)
    filename: str | None = None


@router.post("/dual/start")
def start_dual_acquisition(request: DualAcquisitionRequest):
    try:
        return dual_plate_service.start(
            adapter=request.adapter,
            duration=request.duration,
            tare_duration=request.tare_duration,
            sync_tolerance_ms=request.sync_tolerance_ms,
            filename=request.filename,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dual/stop")
def stop_dual_acquisition():
    return dual_plate_service.stop()


@router.get("/dual/status")
def dual_acquisition_status():
    return dual_plate_service.status()
