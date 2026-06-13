import httpx
import asyncio
from app.core.config import settings

TFL_BASE = "https://api.tfl.gov.uk"

async def get_tfl_data(endpoint, params=None):
    """TfL API 공통 호출 함수 (에러 처리 강화)"""
    if params is None: params = {}
    params["app_key"] = settings.TFL_APP_KEY
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.get(f"{TFL_BASE}{endpoint}", params=params)
            data = res.json()
            # 로그에 찍힌 에러 방지: 데이터가 리스트인지 확인
            if res.status_code != 200 or isinstance(data, str):
                return None
            return data
        except Exception:
            return None

async def get_bus_arrivals(stop_id):
    """실시간 버스 도착 정보 (신호등 예측용)"""
    return await get_tfl_data(f"/StopPoint/{stop_id}/Arrivals")

async def get_road_disruptions():
    """실시간 도로 사고/공사 정보 (우회 경로 판단용)"""
    return await get_tfl_data("/Road/All/Disruption")

async def get_nearby_jamcams(lat, lon, radius=500):
    """주변 도로 영상(CCTV) 주소 가져오기"""
    data = await get_tfl_data("/Place", {
        "type": "JamCam",
        "lat": lat,
        "lon": lon,
        "radius": radius
    })
    if not data: return []
    # 영상 URL과 위치 정보만 추출
    return [{
        "id": c.get("id"),
        "name": c.get("commonName"),
        "imageUrl": next((p["value"] for p in c.get("additionalProperties", []) if p["key"] == "imageUrl"), None),
        "lat": c.get("lat"),
        "lon": c.get("lon")
    } for c in data]
