from pydantic import BaseModel
from typing import Optional

# ── 요청 모델 ──────────────────────────────────────────
class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    target_pace_min_per_km: float   # 예: 6.0 = 6분/km
    target_distance_km: float

class SessionSaveRequest(BaseModel):
    user_id: str
    route_id: str
    distance_km: float
    duration_sec: int
    avg_pace_min_per_km: float
    avg_heart_rate: Optional[int] = None
    calories_burned: Optional[float] = None

# ── 응답 모델 ──────────────────────────────────────────
class Coordinate(BaseModel):
    lat: float
    lon: float

class SignalInfo(BaseModel):
    location: Coordinate
    red_duration_sec: int           # 평균 빨간불 대기 시간
    crossing_type: str              # "pedestrian" | "signalized"

class RouteOption(BaseModel):
    route_id: str
    name: str
    distance_km: float
    estimated_duration_min: float
    signal_stops: int               # 예상 신호 대기 횟수
    signal_wait_total_sec: int      # 예상 총 대기 시간(초)
    score: float                    # 높을수록 좋음 (0~100)
    polyline: list[Coordinate]      # 지도에 그릴 경로 좌표 목록
    description: str

class RouteResponse(BaseModel):
    routes: list[RouteOption]
    weather_summary: str
    weather_temp_c: float
    weather_icon: str

class SessionResult(BaseModel):
    distance_km: float
    duration_sec: int
    avg_pace_min_per_km: float
    avg_heart_rate: Optional[int]
    calories_burned: float
    efficiency_score: float         # 신호 멈춤 없이 달린 비율
