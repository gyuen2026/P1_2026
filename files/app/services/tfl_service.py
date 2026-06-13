import httpx
import asyncio
from app.core.config import settings

TFL_BASE = "https://api.tfl.gov.uk"

async def get_tfl_data(endpoint, params=None):
    if params is None: params = {}
    params["app_key"] = settings.TFL_APP_KEY
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.get(f"{TFL_BASE}{endpoint}", params=params)
            if res.status_code != 200: return None
            data = res.json()
            return data if not isinstance(data, str) else None
        except: return None

async def get_bus_arrivals(stop_id):
    """G. 실시간 버스 지연 정보"""
    return await get_tfl_data(f"/StopPoint/{stop_id}/Arrivals")

async def get_road_disruptions():
    """N. 실시간 도로 사고/장애 정보"""
    return await get_tfl_data("/Road/All/Disruption")

async def get_nearby_jamcams(lat, lon, radius=500):
    """H. 실시간 도로 영상(CCTV) 위치 및 이미지"""
    data = await get_tfl_data("/Place", {"type": "JamCam", "lat": lat, "lon": lon, "radius": radius})
    if not data: return []
    return [{
        "id": c.get("id"),
        "imageUrl": next((p["value"] for p in c.get("additionalProperties", []) if p["key"] == "imageUrl"), None),
        "lat": c.get("lat"), "lon": c.get("lon")
    } for c in data]

async def get_journey_options(start_lat, start_lon, end_lat, end_lon):
    """A, B. 출발지-목적지 경로 옵션 추출"""
    from_loc = f"{start_lat},{start_lon}"
    to_loc = f"{end_lat},{end_lon}"
    return await get_tfl_data(f"/Journey/JourneyResults/{from_loc}/to/{to_loc}", {"mode": "walking"})
