from fastapi import FastAPI
import asyncio
from app.services import route_service, signal_collector

app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(signal_collector.run_scheduler(interval_minutes=30))

@app.get("/")
async def root(): return {"status": "global_tracking_active"}

@app.get("/routes/recommend")
async def get_routes(start_lat: float, start_lon: float, end_lat: float, end_lon: float, pace: float = 5.5, dist: float = 5.0):
    return {"routes": await route_service.recommend_routes(start_lat, start_lon, end_lat, end_lon, pace, dist)}

@app.get("/routes/check-status")
async def check_status(lat: float, lon: float, hr: int = 0, pace: float = 0):
    return await route_service.check_route_integrity(lat, lon, hr, pace)
