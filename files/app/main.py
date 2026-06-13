from fastapi import FastAPI, Query
from app.services import route_service, signal_collector
import asyncio

app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(signal_collector.run_scheduler(interval_minutes=30))

@app.get("/")
async def root(): return {"status": "ok"}

@app.get("/routes/recommend")
async def get_routes(start_lat: float, start_lon: float, end_lat: float, end_lon: float, pace: float = 5.5, dist: float = 5.0):
    """5개 최적 경로 추천 API"""
    routes = await route_service.recommend_routes(start_lat, start_lon, end_lat, end_lon, pace, dist)
    return {"routes": routes}

@app.get("/routes/check-status")
async def check_status(lat: float, lon: float, hr: int = 0, pace: float = 0):
    """러닝 중 실시간 보이스 시나리오 API"""
    return await route_service.check_route_integrity(lat, lon, hr, pace)
