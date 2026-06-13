import httpx
import asyncio
from app.core.config import settings

TFL_BASE = "https://api.tfl.gov.uk"

async def get_all_stops_in_zones(lat=51.5074, lon=-0.1278, radius=15000):
    """런던 중심 기준 15km 반경(1~3존) 내의 모든 버스 정류장 찾기"""
    params = {
        "lat": lat, "lon": lon, "radius": radius,
        "stopTypes": "NaptanPublicBusCoachTram",
        "app_key": settings.TFL_APP_KEY
    }
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            res = await client.get(f"{TFL_BASE}/StopPoint", params=params)
            data = res.json()
            return data.get("stopPoints", [])
        except: return []

async def get_all_jamcams():
    """런던 전역의 모든 JamCam 이미지 데이터 수집"""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            res = await client.get(f"{TFL_BASE}/Place/Type/JamCam", params={"app_key": settings.TFL_APP_KEY})
            return res.json()
        except: return []

# 기존 get_bus_arrivals, get_road_disruptions 등은 유지
