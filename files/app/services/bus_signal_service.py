"""
버스 실시간 도착 데이터로 신호등 사이클 역추적 서비스

원리:
  버스 예정 도착시간 vs 실제 도착시간의 차이 = 신호 대기 시간
  이 패턴을 누적하면 신호 사이클을 역추적 가능
"""
import httpx
import math
from datetime import datetime, timezone
from app.core.config import settings

TFL_BASE = "https://api.tfl.gov.uk"


# ── 1. 경로 근처 버스 정류장 찾기 ──────────────────────────────────
async def get_stops_near_path(waypoints: list[dict], radius_m: int = 60) -> list[dict]:
    """
    경로 좌표 근처 버스 정류장 목록 반환
    신호등이 있는 교차로 근처 정류장만 유효
    """
    stops = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=10) as client:
        # 경로를 4개 구간으로 샘플링 (API 호출 최소화)
        sample_points = waypoints[::max(1, len(waypoints)//4)]

        for pt in sample_points:
            params = {
                "app_key": settings.TFL_APP_KEY,
                "lat": pt["lat"],
                "lon": pt["lon"],
                "radius": radius_m,
                "stopTypes": "NaptanPublicBusCoachTram",
                "returnLines": "false",
            }
            try:
                res = await client.get(f"{TFL_BASE}/StopPoint", params=params)
                if res.status_code == 200:
                    data = res.json()
                    for stop in data.get("stopPoints", []):
                        sid = stop.get("id")
                        if sid and sid not in seen_ids:
                            seen_ids.add(sid)
                            stops.append({
                                "id": sid,
                                "name": stop.get("commonName", ""),
                                "lat": stop.get("lat"),
                                "lon": stop.get("lon"),
                            })
            except Exception:
                continue

    return stops


# ── 2. 버스 도착 지연 데이터로 신호 대기 시간 추출 ──────────────────
async def get_signal_wait_estimate(stop_id: str) -> dict:
    """
    버스 정류장의 실시간 도착 데이터 분석
    예정 도착 vs 실제 도착 차이 → 신호 대기 추정

    반환:
      estimated_wait_sec: 예상 신호 대기 시간(초)
      cycle_sec: 추정 신호 사이클(초)
      confidence: 데이터 신뢰도 (0~1)
    """
    params = {"app_key": settings.TFL_APP_KEY}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.get(
                f"{TFL_BASE}/StopPoint/{stop_id}/Arrivals",
                params=params
            )
            if res.status_code != 200:
                return _default_signal_estimate()

            arrivals = res.json()
            if not arrivals:
                return _default_signal_estimate()

            # 지연 시간 추출
            delays = []
            for bus in arrivals:
                expected = bus.get("expectedArrival", "")
                scheduled = bus.get("scheduledArrival", "")
                if expected and scheduled:
                    try:
                        exp_t = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                        sch_t = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                        delay = (exp_t - sch_t).total_seconds()
                        # 양의 지연만 (신호 대기로 인한 지연)
                        if 10 < delay < 120:
                            delays.append(delay)
                    except Exception:
                        continue

            if not delays:
                return _default_signal_estimate()

            avg_delay = sum(delays) / len(delays)
            confidence = min(1.0, len(delays) / 5)  # 5개 이상이면 신뢰도 1.0

            # 버스 지연 → 신호 사이클 추정
            # 버스 지연의 약 70%가 신호 대기에서 발생한다고 가정
            signal_wait = avg_delay * 0.7
            # 신호 사이클 = 대기 * 2 (빨간 + 초록이 비슷한 비율이라고 가정)
            cycle_estimate = signal_wait * 2.8

            return {
                "estimated_wait_sec": round(signal_wait),
                "cycle_sec": round(min(max(cycle_estimate, 45), 150)),  # 45~150초 범위 제한
                "confidence": round(confidence, 2),
                "sample_count": len(delays),
            }

        except Exception:
            return _default_signal_estimate()


def _default_signal_estimate() -> dict:
    """데이터 없을 때 TfL 공식 기준값 사용 (30초)"""
    return {
        "estimated_wait_sec": 30,
        "cycle_sec": 90,
        "confidence": 0.1,
        "sample_count": 0,
    }


# ── 3. 핵심: 러너가 신호등에 도착할 때 초록불일 확률 계산 ───────────
def calc_green_probability(
    arrival_sec_from_now: float,  # 러너가 신호등에 도착하는 시간 (초)
    cycle_sec: float,             # 신호 사이클 (초)
    green_ratio: float = 0.45,    # 초록불 비율 (런던 평균)
) -> float:
    """
    러너가 특정 신호등에 도착할 때 초록불일 확률

    런던 신호 구조:
      보행자 초록불 = 자동차 빨간불 구간
      자동차 초록불이 전체 사이클의 약 55%
      보행자 초록불이 전체 사이클의 약 45%

    반환: 0.0 (확실히 빨간) ~ 1.0 (확실히 초록)
    """
    if cycle_sec <= 0:
        return green_ratio

    # 사이클 내 도착 위치 (0 ~ cycle_sec)
    position_in_cycle = arrival_sec_from_now % cycle_sec

    # 초록불 구간 = 사이클의 앞 green_ratio 부분
    green_duration = cycle_sec * green_ratio

    if position_in_cycle <= green_duration:
        # 초록불 구간에 도착
        # 구간 중앙에 가까울수록 높은 확률 (끝부분은 불확실)
        margin = green_duration * 0.2  # 20% 마진
        if position_in_cycle < margin or position_in_cycle > green_duration - margin:
            return 0.6  # 경계 근처 = 불확실
        return 0.9  # 중앙 = 높은 확률
    else:
        # 빨간불 구간에 도착
        red_duration = cycle_sec - green_duration
        position_in_red = position_in_cycle - green_duration
        margin = red_duration * 0.2

        if position_in_red < margin or position_in_red > red_duration - margin:
            return 0.4  # 경계 근처 = 불확실
        return 0.1  # 중앙 = 낮은 확률


# ── 4. 시간대별 가중치 ────────────────────────────────────────────
def get_time_weight(hour: int) -> float:
    """
    시간대별 신호 대기 확률 가중치
    러시아워에 보행자 버튼 수요가 높아 신호 대기 가능성 증가
    """
    if 7 <= hour <= 9:    # 아침 러시
        return 1.4
    elif 17 <= hour <= 19: # 저녁 러시
        return 1.5
    elif 12 <= hour <= 14: # 점심
        return 1.2
    elif 22 <= hour or hour <= 6:  # 심야
        return 0.4  # 보행자 적어서 신호 대기 거의 없음
    else:
        return 1.0  # 평시


# ── 5. 경로 전체 빨간불 조우 확률 계산 ───────────────────────────────
async def calc_route_red_probability(
    waypoints: list[dict],
    pace_min_per_km: float,
    depart_time: datetime | None = None,
) -> dict:
    """
    경로 전체에서 빨간불을 만날 예상 횟수와 확률 계산

    반환:
      expected_red_stops: 예상 빨간불 멈춤 횟수
      total_wait_sec: 예상 총 대기 시간(초)
      red_probability: 전체 경로 빨간불 조우 확률 (0~1)
      green_wave_score: 초록불 연속 확률 점수 (0~100)
    """
    if depart_time is None:
        depart_time = datetime.now(timezone.utc)

    hour = depart_time.hour
    time_weight = get_time_weight(hour)

    # 경로 위 버스 정류장 = 신호등 위치 프록시
    stops = await get_stops_near_path(waypoints)

    if not stops:
        # 정류장 없으면 기본값
        return {
            "expected_red_stops": 0,
            "total_wait_sec": 0,
            "red_probability": 0.3,
            "green_wave_score": 70,
            "stop_count": 0,
        }

    total_distance_km = 0.0
    red_stops = 0
    total_wait = 0.0
    green_probs = []

    for i, stop in enumerate(stops):
        # 이 정류장까지 러너 도착 예상 시간
        dist_to_stop = _haversine_km(
            waypoints[0]["lat"], waypoints[0]["lon"],
            stop["lat"], stop["lon"]
        )
        arrival_sec = dist_to_stop * pace_min_per_km * 60

        # 버스 지연 데이터로 신호 추정
        signal_data = await get_signal_wait_estimate(stop["id"])
        cycle = signal_data["cycle_sec"] * time_weight
        wait = signal_data["estimated_wait_sec"] * time_weight

        # 초록불 확률 계산
        green_prob = calc_green_probability(arrival_sec, cycle)
        green_probs.append(green_prob)

        # 빨간불이면 대기 추가
        if green_prob < 0.5:
            red_stops += 1
            total_wait += wait

    # 전체 경로 초록불 연속 확률
    if green_probs:
        avg_green = sum(green_probs) / len(green_probs)
        # 모든 신호에서 초록불일 확률 (곱)
        all_green = 1.0
        for p in green_probs:
            all_green *= p
    else:
        avg_green = 0.7
        all_green = 0.7

    green_wave_score = round(avg_green * 100, 1)

    return {
        "expected_red_stops": red_stops,
        "total_wait_sec": round(total_wait),
        "red_probability": round(1 - avg_green, 2),
        "green_wave_score": green_wave_score,
        "stop_count": len(stops),
        "confidence": signal_data.get("confidence", 0.1),
    }


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ── 6. 학습 패턴 우선 사용 (수집기 연동) ─────────────────────────
async def get_signal_estimate_with_learning(
    stop_id: str,
    hour: int,
    dow: int,
) -> dict:
    """
    학습된 패턴 있으면 우선 사용, 없으면 실시간 버스 데이터 사용
    정확도: 학습패턴(높음) > 실시간버스(중간) > 기본값(낮음)
    """
    # 1순위: 학습된 패턴
    try:
        from app.services.signal_collector import get_learned_pattern
        pattern = await get_learned_pattern(stop_id, hour, dow)
        if pattern and pattern.get("observation_count", 0) >= 3:
            return {
                "estimated_wait_sec": pattern["avg_wait_sec"],
                "cycle_sec": pattern["avg_cycle_sec"],
                "confidence": min(0.9, pattern["observation_count"] / 20),
                "source": "learned",
            }
    except Exception:
        pass

    # 2순위: 실시간 버스 데이터
    result = await get_signal_wait_estimate(stop_id)
    result["source"] = "realtime"
    return result
