from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.crowd_signal_service import submit_report, get_consensus_near
from app.services.fusion_service import estimate_free_tier_accuracy

router = APIRouter(prefix="/signals", tags=["signals"])


class SignalReportRequest(BaseModel):
    lat: float
    lon: float
    reported_color: str = Field(..., description="GREEN, RED, AMBER, or WAITING")
    waited_sec: float = 0.0
    stop_id: str | None = None


@router.post("/report")
async def report_signal(req: SignalReportRequest):
    """Runner crowd report — free substitute for paid traffic APIs."""
    return await submit_report(
        lat=req.lat,
        lon=req.lon,
        reported_color=req.reported_color,
        waited_sec=req.waited_sec,
        stop_id=req.stop_id,
    )


@router.get("/consensus")
async def signal_consensus(lat: float, lon: float):
    return await get_consensus_near(lat, lon)


@router.get("/accuracy")
async def free_tier_accuracy():
    return estimate_free_tier_accuracy()
