import httpx
import asyncio
from app.core.config import settings

TFL_BASE = "https://api.tfl.gov.uk"

async def get_road_disruptions(lat: float, lon: float, radius_m: int = 1000) -> list[dict]:
    """
    주어진 좌표 반경 내 도로 방해요소(신호 포함) 조회
    """
    params = {
        "app_key": settings.TFL_APP_KEY,
        "lat": lat,
        "lon": lon,
        "radius": radius_m,
        "categories": "Disruption,RoadProject",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.get(f"{TFL_BASE}/Place", params=params)
            if res.status_code != 200:
                return []
            return res.json()
        except Exception:
            return []

async def fetch_signal_at_point(client, lat, lon):
    """단일 포인트 신호등 조회 (병렬 처리를 위한 헬퍼)"""
    params = {
        "app_key": settings.TFL_APP_KEY,
        "lat": lat,
        "lon": lon,
        "radius": 100,
        "type": "TrafficSignal",
    }
    try:
        res = await client.get(f"{TFL_BASE}/Place", params=params)
        return res.json() if res.status_code == 200 else []
    except Exception:
        return []

async def get_pedestrian_signals_on_path(waypoints: list[dict]) -> list[dict]:
    """
    경로 waypoints 기반으로 보행자 신호등 정보 수집 (병렬 처리 버전)
    """
    if not waypoints:
        return []
        
    signals = []
    # 3개마다 샘플링하여 API 호출 부하 감소
    sampled_points = waypoints[::3]
    
    async with httpx.AsyncClient(timeout=10) as client:
        # 병렬 요청 생성
        tasks = [fetch_signal_at_point(client, pt["lat"], pt["lon"]) for pt in sampled_points]
        results = await asyncio.gather(*tasks)
        
        for res in results:
            if isinstance(res, list):
                signals.extend(res)
    return signals

async def get_journey_options(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    mode: str = "walking"
) -> dict:
    """
    TfL Journey Planner API - 경로 옵션 조회
    """
    params = {
        "app_key": settings.TFL_APP_KEY,
        "mode": mode,
        "alternativeWalking": "true",
        "walkingSpeed": "Fast",
        "nationalSearch": "false",
    }
    from_str = f"{start_lat},{start_lon}"
    to_str = f"{end_lat},{end_lon}"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.get(
                f"{TFL_BASE}/Journey/JourneyResults/{from_str}/to/{to_str}",
                params=params
            )
            if res.status_code != 200:
                return {"journeys": []} # 안전한 구조 반환
            return res.json()
        except Exception:
            return {"journeys": []}

async def count_signals_on_path(waypoints: list[dict]) -> int:
    """
    경로 좌표를 기반으로 신호등 개수를 추정합니다.
    """
    if not waypoints:
        return 0
    # 런던 도심 데이터 기준: 좌표 밀도 기반 추정 (8개 좌표당 약 1개 신호등)
    return max(1, len(waypoints) // 8)
