from fastapi import FastAPI, Query
import asyncio
from app.services import route_service, signal_collector

app = FastAPI(title="London Runner Smart Coach")

@app.on_event("startup")
async def startup_event():
    # 백그라운드에서 신호 수집 스케줄러 실행
    asyncio.create_task(signal_collector.run_scheduler(interval_minutes=30))

@app.get("/")
async def root():
    return {"status": "active", "system": "London Runner Hybrid Engine"}

@app.get("/routes/recommend")
async def get_smart_routes(
    start_lat: float, start_lon: float, 
    end_lat: float, end_lon: float, 
    pace: float = 5.5, dist: float = 5.0
):
    """I. 최적 경로 5개 추천 (E, F, G, H 고려)"""
    routes = await route_service.recommend_routes(start_lat, start_lon, end_lat, end_lon, pace, dist)
    return {"recommended_routes": routes}

@app.get("/routes/check-status")
async def get_realtime_coaching(
    lat: float, lon: float, hr: int, pace: float
):
    """J~N. 실시간 러닝 상태 분석 및 보이스 시나리오 반환"""
    return await route_service.check_route_integrity(lat, lon, hr, pace)
