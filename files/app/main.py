from fastapi import FastAPI, Query
from datetime import datetime, timezone
import asyncio

# 프로젝트 내부 모듈 임포트
from app.services.route_service import recommend_routes
from app.services.signal_collector import run_scheduler

app = FastAPI(title="London Runner API")

# --- 서버 시작 시 실행되는 이벤트 ---
@app.on_event("startup")
async def startup_event():
    """
    서버가 켜질 때 신호 수집기(Scheduler)를 백그라운드에서 실행합니다.
    서버 응답(API)에 영향을 주지 않고 별도로 30분마다 돌아갑니다.
    """
    print("🚀 Starting Signal Collector Scheduler...")
    asyncio.create_task(run_scheduler(interval_minutes=30))

# --- 기본 홈 화면 ---
@app.get("/")
async def root():
    return {"message": "London Runner API is running", "mode": "Smart Signal Tracking Enabled"}

# --- 핵심 기능: 경로 추천 API ---
@app.get("/routes/recommend")
async def get_recommendations(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    pace: float = Query(5.5, description="Pace in min/km"),
    distance: float = Query(5.0, description="Target distance in km")
):
    """
    사용자의 페이스와 목적지를 입력받아 
    빨간불 확률이 가장 낮은 최적 경로 7개를 반환합니다.
    """
    routes = await recommend_routes(
        start_lat=start_lat, start_lon=start_lon,
        end_lat=end_lat, end_lon=end_lon,
        target_pace=pace,
        target_km=distance,
        depart_time=datetime.now(timezone.utc)
    )
    return {"routes": routes}
