from fastapi import FastAPI
import asyncio

from app.api.routes import router as routes_router
from app.api.sessions import router as sessions_router
from app.services import route_service, signal_collector

app = FastAPI(title="London Runner API", version="2.0")

app.include_router(routes_router)
app.include_router(sessions_router)


@app.on_event("startup")
async def startup():
    asyncio.create_task(signal_collector.run_scheduler(interval_minutes=30))


@app.get("/")
async def root():
    return {"status": "global_tracking_active", "zones": "1-2", "radius_km": 7.5}


@app.get("/routes/recommend")
async def get_routes(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    pace: float = 5.5,
    dist: float = 5.0,
):
    return {
        "routes": await route_service.recommend_routes(
            start_lat, start_lon, end_lat, end_lon, pace=pace, dist=dist
        )
    }


@app.get("/routes/check-status")
async def check_status(
    lat: float,
    lon: float,
    hr: int = 0,
    pace: float = 0,
    speed_kmh: float = 0,
):
    """
    Real-time coaching: monitors position (J/M), heart rate (K), speed (L),
    and road disruptions (N). Returns voice rerouting instructions.
    """
    return await route_service.check_route_integrity(
        user_lat=lat,
        user_lon=lon,
        hr=hr,
        pace=pace,
        speed_kmh=speed_kmh if speed_kmh > 0 else None,
    )
