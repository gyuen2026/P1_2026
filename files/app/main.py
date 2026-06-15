from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio

from app.api.geocode import router as geocode_router
from app.api.routes import router as routes_router
from app.api.sessions import router as sessions_router
from app.api.signals import router as signals_router
from app.services import route_service, signal_collector

app = FastAPI(title="London Runner API", version="2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_router)
app.include_router(sessions_router)
app.include_router(signals_router)
app.include_router(geocode_router)


@app.on_event("startup")
async def startup():
    from app.services.osm_crossings import ensure_crossings_loaded
    # Background collector: runs every 90 min, parallel with user API requests.
    # For 30-day 24/7 collection, also run scripts/start_month_collector.sh locally.
    asyncio.create_task(signal_collector.run_scheduler(interval_minutes=90))
    asyncio.create_task(ensure_crossings_loaded())


@app.get("/collector/accuracy")
async def collector_accuracy():
    """Free-tier accuracy tiers (matches paid traffic-API fusion target)."""
    from app.services.fusion_service import estimate_free_tier_accuracy
    return estimate_free_tier_accuracy()


@app.get("/collector/status")
async def collector_status():
    """Check if background collection is running and when it last finished."""
    return signal_collector.get_collection_status()


@app.post("/collector/run")
async def collector_run():
    """
    Manually start one collection cycle.
    Use with an external cron (e.g. cron-job.org every 10 min) to keep Render awake.
    """
    asyncio.create_task(signal_collector.trigger_collection())
    return {"status": "started", **signal_collector.get_collection_status()}


@app.get("/")
async def root():
    return {
        "status": "global_tracking_active",
        "zones": "1-2",
        "radius_km": 7.5,
        "collector_interval_min": 90,
        "collector": signal_collector.get_collection_status(),
    }


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
