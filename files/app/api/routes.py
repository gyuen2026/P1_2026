from fastapi import APIRouter, HTTPException
from app.models.route import RouteRequest, RouteResponse, SessionSaveRequest
from app.predict.route_service import recommend_routes, check_route_integrity
from app.predict.weather_service import get_current_weather, get_weather_summary

router = APIRouter(prefix="/routes", tags=["routes"])

@router.post("/recommend", response_model=RouteResponse)
async def get_route_recommendations(req: RouteRequest):
    """
    출발지/목적지/페이스/거리 기반 최적 러닝 경로 5~10개 반환
    신호 대기 최소화 + 날씨 반영 점수 순 정렬
    """
    try:
        routes = await recommend_routes(
            start_lat=req.start_lat,
            start_lon=req.start_lon,
            end_lat=req.end_lat,
            end_lon=req.end_lon,
            target_pace=req.target_pace_min_per_km,
            target_km=req.target_distance_km,
        )
        weather = await get_current_weather(req.start_lat, req.start_lon)
        return RouteResponse(
            routes=routes,
            weather_summary=get_weather_summary(weather),
            weather_temp_c=weather["temp_c"],
            weather_icon=weather["icon"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/weather")
async def get_weather(lat: float, lon: float):
    """현재 위치 날씨 조회"""
    weather = await get_current_weather(lat, lon)
    return {
        "summary": get_weather_summary(weather),
        **weather
    }


@router.get("/check-status")
async def check_route_status(
    lat: float,
    lon: float,
    hr: int = 0,
    pace: float = 0,
    speed_kmh: float = 0,
    crossing_lat: float | None = None,
    crossing_lon: float | None = None,
    crossing_index: int | None = None,
):
    """Real-time rerouting and coaching during an active run."""
    return await check_route_integrity(
        user_lat=lat,
        user_lon=lon,
        hr=hr,
        pace=pace,
        speed_kmh=speed_kmh if speed_kmh > 0 else None,
        crossing_lat=crossing_lat,
        crossing_lon=crossing_lon,
        crossing_index=crossing_index,
    )
