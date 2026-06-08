"""
경로 추천 서비스 — 핵심 목표:
"빨간불을 만날 가능성 최소화" + "사용자 페이스/거리 반영"
= 심박수 유지 → 훈련 효율 극대화
"""
import math
import uuid
from datetime import datetime, timezone
from app.models.route import RouteOption, Coordinate
from app.services.tfl_service import get_journey_options, count_signals_on_path
from app.services.weather_service import get_current_weather
from app.services.bus_signal_service import calc_route_red_probability


# ── 거리 계산 ──────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def calc_route_distance(waypoints: list[dict]) -> float:
    total = 0.0
    for i in range(len(waypoints) - 1):
        total += haversine_km(
            waypoints[i]["lat"], waypoints[i]["lon"],
            waypoints[i+1]["lat"], waypoints[i+1]["lon"]
        )
    return round(total, 2)


# ── 폴백 경로 생성 ─────────────────────────────────────────────────
def _generate_fallback_routes(start: tuple, end: tuple, count: int = 7) -> list[list[dict]]:
    offsets = [
        (0.0,    0.0),
        (0.003,  0.002),
        (-0.003, 0.002),
        (0.002, -0.003),
        (-0.002,-0.003),
        (0.005,  0.0),
        (0.0,    0.005),
    ]
    routes = []
    for dlat, dlon in offsets[:count]:
        mid = ((start[0]+end[0])/2 + dlat, (start[1]+end[1])/2 + dlon)
        n = 6
        waypoints = []
        for i in range(n):
            t = i / (n - 1)
            waypoints.append({
                "lat": start[0] + (mid[0]-start[0]) * t,
                "lon": start[1] + (mid[1]-start[1]) * t,
            })
        for i in range(1, n):
            t = i / (n - 1)
            waypoints.append({
                "lat": mid[0] + (end[0]-mid[0]) * t,
                "lon": mid[1] + (end[1]-mid[1]) * t,
            })
        routes.append(waypoints)
    return routes


# ── 핵심 점수 계산: 빨간불 최소화 중심 ────────────────────────────
def score_route(
    green_wave_score: float,    # 초록불 연속 확률 (0~100) ← 핵심
    expected_red_stops: int,    # 예상 빨간불 횟수
    total_wait_sec: int,        # 예상 총 대기 시간
    distance_km: float,
    target_km: float,
    weather: dict,
) -> float:
    """
    경로 점수 (0~100) — 빨간불 최소화가 핵심

    가중치:
      초록불 연속성  50점 ← 핵심 목표
      빨간불 횟수    20점
      총 대기시간    15점
      목표거리 근접  10점
      날씨 보정       5점
    """
    # 초록불 연속 점수 (50점)
    green_score = green_wave_score * 0.5

    # 빨간불 횟수 점수 (20점): 0개=20점, 1개당 -5점
    red_count_score = max(0, 20 - expected_red_stops * 5)

    # 대기시간 점수 (15점): 0초=15점, 30초당 -5점
    wait_score = max(0, 15 - (total_wait_sec / 30) * 5)

    # 거리 점수 (10점)
    dist_diff = abs(distance_km - target_km) / max(target_km, 1)
    distance_score = max(0, 10 * (1 - dist_diff * 2))

    # 날씨 점수 (5점)
    weather_score = 5 if not weather.get("is_rain") and not weather.get("is_windy") else 2

    return round(min(100, green_score + red_count_score + wait_score + distance_score + weather_score), 1)


ROUTE_NAMES = [
    ("⚡ Volt Non-Stop",        "Maximum green wave — signal-free flow predicted"),
    ("🌱 Green Park Way",       "Park paths — low pedestrian button demand"),
    ("🏙️ Grid Shortcut",       "Shortest distance — synchronized signal timing"),
    ("🌉 Bridge Interval",      "Bridge route — minimal crossing signals"),
    ("🌊 Thames Riverside",     "Riverside path — low traffic signal density"),
    ("🏛️ Cultural Quarter",    "Southbank route — moderate signal prediction"),
    ("🌳 Jubilee Gardens Loop", "Garden detour — shaded, low signal zone"),
]


# ── 메인 추천 함수 ─────────────────────────────────────────────────
async def recommend_routes(
    start_lat: float, start_lon: float,
    end_lat: float,   end_lon: float,
    target_pace: float,
    target_km: float,
    depart_time: datetime | None = None,
) -> list[RouteOption]:

    if depart_time is None:
        depart_time = datetime.now(timezone.utc)

    # 1. 날씨 조회
    weather = await get_current_weather(start_lat, start_lon)

    # 2. TfL 실제 경로 시도 → 실패 시 폴백
    tfl_journeys = await get_journey_options(start_lat, start_lon, end_lat, end_lon)
    if tfl_journeys:
        raw_routes = [j["waypoints"] for j in tfl_journeys]
        if len(raw_routes) < 5:
            fallbacks = _generate_fallback_routes(
                (start_lat, start_lon), (end_lat, end_lon),
                count=7 - len(raw_routes)
            )
            raw_routes.extend(fallbacks)
    else:
        raw_routes = _generate_fallback_routes(
            (start_lat, start_lon), (end_lat, end_lon), count=7
        )

    # 3. 각 경로 평가
    options = []
    for i, waypoints in enumerate(raw_routes[:7]):

        # 핵심: 버스 데이터로 빨간불 예측
        red_data = await calc_route_red_probability(
            waypoints, target_pace, depart_time
        )

        distance = calc_route_distance(waypoints)
        running_min = distance * target_pace
        total_min = running_min + red_data["total_wait_sec"] / 60

        score = score_route(
            green_wave_score=red_data["green_wave_score"],
            expected_red_stops=red_data["expected_red_stops"],
            total_wait_sec=red_data["total_wait_sec"],
            distance_km=distance,
            target_km=target_km,
            weather=weather,
        )

        name, desc = ROUTE_NAMES[i % len(ROUTE_NAMES)]
        polyline = [Coordinate(lat=w["lat"], lon=w["lon"]) for w in waypoints]

        options.append(RouteOption(
            route_id=str(uuid.uuid4()),
            name=name,
            distance_km=distance,
            estimated_duration_min=round(total_min, 1),
            signal_stops=red_data["expected_red_stops"],
            signal_wait_total_sec=red_data["total_wait_sec"],
            score=score,
            polyline=polyline,
            description=f"{desc} | 🟢 Green wave: {red_data['green_wave_score']}%",
        ))

    options.sort(key=lambda r: r.score, reverse=True)
    return options[:7]
