import httpx
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
            res.raise_for_status()
            return res.json()
        except Exception:
            return []

async def get_pedestrian_signals_on_path(waypoints: list[tuple[float, float]]) -> list[dict]:
    """
    경로 waypoints 기반으로 보행자 신호등 정보 수집
    각 구간마다 TfL Place API로 신호등 포인트 탐색
    """
    signals = []
    async with httpx.AsyncClient(timeout=10) as client:
        for lat, lon in waypoints[::3]:  # 3개마다 샘플링 (API 호출 최소화)
            params = {
                "app_key": settings.TFL_APP_KEY,
                "lat": lat,
                "lon": lon,
                "radius": 100,
                "type": "TrafficSignal",
            }
            try:
                res = await client.get(f"{TFL_BASE}/Place", params=params)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        signals.extend(data)
            except Exception:
                continue
    return signals

async def get_journey_options(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    mode: str = "walking"
) -> dict:
    """
    TfL Journey Planner API - 경로 옵션 조회
    walking 모드로 보행자 기반 경로를 가져옴
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
            res.raise_for_status()
            return res.json()
        except Exception as e:
            return {}
