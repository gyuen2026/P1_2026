from fastapi import APIRouter, HTTPException
from app.models.route import SessionSaveRequest, SessionResult
from app.core.config import settings
from supabase import create_client
import math

router = APIRouter(prefix="/sessions", tags=["sessions"])

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def calc_calories(distance_km: float, duration_sec: int, avg_heart_rate: int | None) -> float:
    """
    칼로리 계산 (MET 기반 간이 공식)
    평균 체중 70kg 기준, 추후 사용자 프로필에서 실측값으로 교체
    """
    weight_kg = 70
    met = 9.8  # 러닝 MET (6min/km 기준)
    hours = duration_sec / 3600
    return round(met * weight_kg * hours, 1)

def calc_efficiency_score(
    total_sec: int, signal_wait_sec: int
) -> float:
    """신호 없이 달린 비율 (0~100)"""
    if total_sec == 0:
        return 100.0
    running_sec = max(0, total_sec - signal_wait_sec)
    return round((running_sec / total_sec) * 100, 1)

@router.post("/save", response_model=SessionResult)
async def save_session(req: SessionSaveRequest):
    """러닝 세션 결과 저장 + 통계 계산"""
    calories = calc_calories(req.distance_km, req.duration_sec, req.avg_heart_rate)
    efficiency = calc_efficiency_score(req.duration_sec, 0)  # signal_wait은 추후 추가

    record = {
        "user_id": req.user_id,
        "route_id": req.route_id,
        "distance_km": req.distance_km,
        "duration_sec": req.duration_sec,
        "avg_pace_min_per_km": req.avg_pace_min_per_km,
        "avg_heart_rate": req.avg_heart_rate,
        "calories_burned": calories,
        "efficiency_score": efficiency,
    }

    try:
        db = get_supabase()
        db.table("running_sessions").insert(record).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB save failed: {e}")

    return SessionResult(
        distance_km=req.distance_km,
        duration_sec=req.duration_sec,
        avg_pace_min_per_km=req.avg_pace_min_per_km,
        avg_heart_rate=req.avg_heart_rate,
        calories_burned=calories,
        efficiency_score=efficiency,
    )

@router.get("/history/{user_id}")
async def get_session_history(user_id: str):
    """사용자 러닝 기록 조회"""
    try:
        db = get_supabase()
        result = db.table("running_sessions")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(20)\
            .execute()
        return {"sessions": result.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
