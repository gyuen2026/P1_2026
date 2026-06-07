import math
import uuid
from app.models.route import RouteOption, Coordinate
from app.services.tfl_service import get_journey_options, get_pedestrian_signals_on_path
from app.services.weather_service import get_current_weather

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def _interpolate_waypoints(
    start: tuple, end: tuple, num_points: int = 8
) -> list[Coordinate]:
    """시작-끝 사이를 num_points 개로 선형 보간"""
    lats = [start[0] + (end[0]-start[0]) * i/(num_points-1) for i in range(num_points)]
    lons = [start[1] + (end[1]-start[1]) * i/(num_points-1) for i in range(num_points)]
    return [Coordinate(lat=la, lon=lo) for la, lo in zip(lats, lons)]

def _generate_route_variants(
    start: tuple, end: tuple, target_km: float, count: int = 7
) -> list[list[Coordinate]]:
    """
    출발-도착 기반으로 다양한 경로 변형 생성
    실제 서비스에서는 TfL Journey API의 alternativeWalking 결과를 파싱하여 대체
    """
    routes = []
    offsets = [
        (0.0, 0.0),         # 직선
        (0.003, 0.002),     # 북동쪽 우회
        (-0.003, 0.002),    # 남동쪽 우회
        (0.002, -0.003),    # 북서쪽 우회
        (-0.002, -0.003),   # 남서쪽 우회
        (0.005, 0.0),       # 북쪽 크게 우회
        (0.0, 0.005),       # 동쪽 크게 우회
    ]
    for i, (dlat, dlon) in enumerate(offsets[:count]):
        mid = ((start[0]+end[0])/2 + dlat, (start[1]+end[1])/2 + dlon)
        waypoints = [
            Coordinate(lat=start[0], lon=start[1]),
            *_interpolate_waypoints(start, mid, 4)[1:-1],
            Coordinate(lat=mid[0], lon=mid[1]),
            *_interpolate_waypoints(mid, end, 4)[1:-1],
            Coordinate(lat=end[0], lon=end[1]),
        ]
        routes.append(waypoints)
    return routes

def _calc_route_distance(waypoints: list[Coordinate]) -> float:
    total = 0.0
    for i in range(len(waypoints)-1):
        total += haversine_km(
            waypoints[i].lat, waypoints[i].lon,
            waypoints[i+1].lat, waypoints[i+1].lon
        )
    return round(total, 2)

def _score_route(
    distance_km: float,
    target_km: float,
    signal_count: int,
    weather: dict,
) -> float:
    """
    경로 점수 계산 (0~100)
    - 신호 적을수록 높은 점수
    - 목표 거리에 가까울수록 높은 점수
    - 비/강풍 시 노출 적은 경로 가점
    """
    # 신호 점수 (신호 0개 = 50점, 1개당 -8점)
    signal_score = max(0, 50 - signal_count * 8)

    # 거리 점수 (목표 거리와 차이가 작을수록 높음)
    dist_diff_ratio = abs(distance_km - target_km) / max(target_km, 1)
    distance_score = max(0, 30 * (1 - dist_diff_ratio * 2))

    # 날씨 보정 (비/강풍이면 실내/지붕 경로 우대 — 여기선 단순히 랜덤성 추가)
    weather_bonus = 0
    if not weather.get("is_rain") and not weather.get("is_windy"):
        weather_bonus = 20

    return round(min(100, signal_score + distance_score + weather_bonus), 1)

ROUTE_NAMES = [
    ("⚡ Volt Non-Stop", "Maximum signal-free flow — riverside underpass priority"),
    ("🌱 Green Park Way", "Scenic park paths with minimal crossings"),
    ("🏙️ Grid Shortcut", "Shortest distance, synchronized crossings"),
    ("🌉 Bridge Interval", "Bridge elevation for cardiac training"),
    ("🌊 Thames Riverside", "Flat riverside path, wind exposed"),
    ("🏛️ Cultural Quarter", "Through Southbank landmarks, moderate signals"),
    ("🌳 Jubilee Gardens Loop", "Garden detour for shaded running"),
]

async def recommend_routes(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    target_pace: float,
    target_km: float,
) -> list[RouteOption]:

    weather = await get_current_weather(start_lat, start_lon)
    route_variants = _generate_route_variants(
        (start_lat, start_lon), (end_lat, end_lon), target_km, count=7
    )

    options = []
    for i, waypoints in enumerate(route_variants):
        # 신호등 데이터 조회
        signal_pts = await get_pedestrian_signals_on_path(
            [(w.lat, w.lon) for w in waypoints]
        )
        signal_count = len(signal_pts)
        # 신호당 평균 대기 30초 가정 (TfL 실측 데이터로 추후 교체)
        signal_wait_total = signal_count * 30

        distance = _calc_route_distance(waypoints)
        duration_min = distance / (1 / target_pace) if target_pace > 0 else distance * target_pace
        duration_min += signal_wait_total / 60  # 신호 대기 시간 포함

        score = _score_route(distance, target_km, signal_count, weather)
        name, desc = ROUTE_NAMES[i % len(ROUTE_NAMES)]

        options.append(RouteOption(
            route_id=str(uuid.uuid4()),
            name=name,
            distance_km=distance,
            estimated_duration_min=round(duration_min, 1),
            signal_stops=signal_count,
            signal_wait_total_sec=signal_wait_total,
            score=score,
            polyline=waypoints,
            description=desc,
        ))

    # 점수 높은 순 정렬
    options.sort(key=lambda r: r.score, reverse=True)
    return options
