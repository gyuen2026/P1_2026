from fastapi import FastAPI, Query
from datetime import datetime, timezone
import asyncio

# 우리가 만든 기능들 불러오기
from app.services.route_service import recommend_routes
from app.services.signal_collector import run_scheduler

app = FastAPI(title="London Runner API")

# --- 서버 시작 시 실행되는 이벤트 ---
@app.on_event("startup")
async def startup_event():
    """서버가 켜질 때 신호 수집기를 백그라운드에서 실행"""
    print("🚀 Starting Signal Collector Scheduler...")
    asyncio.create_task(run_scheduler(interval_minutes=30))

# --- 기본 홈 화면 (서버 생존 확인용) ---
@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "London Runner API is live",
        "time": datetime.now(timezone.utc).isoformat()
    }

# --- 핵심 기능: 경로 추천 API ---
@app.get("/routes/recommend")
async def get_recommendations(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    pace: float = Query(5.5, description="Pace in min/km"),
    distance: float = Query(5.0, description="Target distance in km")
):
    """빨간불 확률이 낮은 최적 경로를 반환"""
    routes = await recommend_routes(
        start_lat=start_lat, start_lon=start_lon,
        end_lat=end_lat, end_lon=end_lon,
        target_pace=pace,
        target_km=distance
    )
    return {"routes": routes}
@app.get("/routes/check-status")
async def check_running_status(
    lat: float, 
    lon: float, 
    route_id: str,
    heart_rate: int = Query(0)
):
    """
    러너가 뛰는 동안 실시간으로 호출하는 API
    심박수와 위치를 분석해 보이스 안내 문구를 반환함
    """
    status = await route_service.check_route_integrity(lat, lon, route_id)
    
    # 심박수 조건 추가 (K 변수)
    if heart_rate > 160:
        status["voice_message"] = f"심박수가 {heart_rate}으로 높습니다. 페이스를 낮추세요. " + status["voice_message"]
        
    return status
