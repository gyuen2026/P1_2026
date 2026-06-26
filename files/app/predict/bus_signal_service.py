"""
버스 실시간 도착 데이터로 신호등 사이클 역추적 서비스

원리:
  버스 예정 도착시간 vs 실제 도착시간의 차이 = 신호 대기 시간
  이 패턴을 누적하면 신호 사이클을 역추적 가능
"""
import asyncio
import httpx
import math
from datetime import datetime
from app.core.config import settings
from app.predict.signal_prediction import get_london_now, london_hour_and_dow

TFL_BASE = "https://api.tfl.gov.uk"


# ── 1. 경로 근처 버스 정류장 찾기 ──────────────────────────────────
async def get_stops_near_path(waypoints: list[dict], radius_m: int = 60) -> list[dict]:
    """
    경로 좌표 근처 버스 정류장 — OSM traffic_signals geofence로 필터 (무료 정확도↑)
    """
    from app.ingest.osm_crossings import ensure_crossings_loaded, is_near_traffic_signal

    crossings = await ensure_crossings_loaded()
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
                        slat, slon = stop.get("lat"), stop.get("lon")
                        if sid and sid not in seen_ids:
                            if slat is not None and slon is not None:
                                if crossings and not is_near_traffic_signal(
                                    float(slat), float(slon), crossings
                                ):
                                    continue
                            seen_ids.add(sid)
                            stops.append({
                                "id": sid,
                                "name": stop.get("commonName", ""),
                                "lat": slat,
                                "lon": slon,
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
            if not arrivals or not isinstance(arrivals, list):
                return _default_signal_estimate()

            from app.predict.signal_prediction import calc_delay_detail

            detail = calc_delay_detail(arrivals, get_london_now())
            if detail["sample_count"] == 0:
                return _default_signal_estimate()

            avg_delay = detail["avg_delay_sec"]
            signal_wait = abs(avg_delay) * 0.7
            cycle_estimate = signal_wait * 2.8

            return {
                "estimated_wait_sec": round(signal_wait),
                "cycle_sec": round(min(max(cycle_estimate, 45), 150)),
                "confidence": detail["confidence"],
                "sample_count": detail["sample_count"],
                "delay_methods": detail.get("methods", []),
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
    *,
    fast: bool = False,
) -> dict:
    """
    경로 전체에서 빨간불을 만날 예상 횟수와 확률 계산

    반환:
      expected_red_stops: 예상 빨간불 멈춤 횟수
      ped_signals_on_path: OSM 보행 신호등 개수 (경로 45m 이내)
      total_wait_sec: 예상 총 대기 시간(초)
      red_probability: 전체 경로 빨간불 조우 확률 (0~1)
      green_wave_score: 초록불 연속 확률 점수 (0~100)
    """
    from app.ingest.osm_crossings import ensure_crossings_loaded, signals_along_path

    if depart_time is None:
        depart_time = get_london_now()
    elif depart_time.tzinfo is None:
        depart_time = depart_time.replace(tzinfo=get_london_now().tzinfo)

    hour = depart_time.astimezone(get_london_now().tzinfo).hour
    hour_i, dow_i = london_hour_and_dow(depart_time)
    time_weight = get_time_weight(hour)

    crossings = await ensure_crossings_loaded()
    osm_signals = signals_along_path(waypoints, crossings, path_buffer_m=45)
    osm_count = len(osm_signals)

    # Fast path: OSM geofence only — no per-stop TfL API (Render timeout fix).
    if fast:
        if osm_count == 0:
            return {
                "expected_red_stops": 1,
                "ped_signals_on_path": 0,
                "total_wait_sec": 25,
                "red_probability": 0.35,
                "green_wave_score": 65.0,
                "stop_count": 0,
                "confidence": 0.12,
                "supabase_learned_stops": 0,
                "supabase_realtime_stops": 0,
                "signal_data_source": "osm_estimate",
            }
        red_rate = min(0.52, 0.28 * time_weight)
        red_stops = max(1, round(osm_count * red_rate))
        total_wait = red_stops * 26
        avg_green = max(0.42, 1.0 - red_stops / osm_count)
        return {
            "expected_red_stops": red_stops,
            "ped_signals_on_path": osm_count,
            "total_wait_sec": total_wait,
            "red_probability": round(1 - avg_green, 2),
            "green_wave_score": round(avg_green * 100, 1),
            "stop_count": osm_count,
            "confidence": 0.2,
            "supabase_learned_stops": 0,
            "supabase_realtime_stops": 0,
            "signal_data_source": "osm_fast",
        }

    # 경로 위 버스 정류장 = 신호 사이클 추정 프록시
    stops = (await get_stops_near_path(waypoints))[:4]

    path_sample = waypoints[:: max(1, len(waypoints) // 40)] or waypoints
    red_stops = 0
    total_wait = 0.0
    green_probs: list[float] = []
    signal_data: dict = {}
    learned_stops = 0
    realtime_stops = 0

    if stops:
        async def _score_stop(stop: dict) -> tuple[float, float, dict]:
            dist_to_stop = min(
                _haversine_km(wp["lat"], wp["lon"], stop["lat"], stop["lon"])
                for wp in path_sample
            )
            arrival_sec = dist_to_stop * pace_min_per_km * 60
            sig = await get_signal_estimate_with_learning(stop["id"], hour_i, dow_i)
            cycle = sig["cycle_sec"] * time_weight
            wait = sig["estimated_wait_sec"] * time_weight
            green_prob = calc_green_probability(arrival_sec, cycle)
            if sig.get("source") == "learned":
                conf = float(sig.get("confidence") or 0)
                green_prob = min(1.0, green_prob + 0.06 * conf)
            return green_prob, wait, sig

        stop_results = await asyncio.gather(*[_score_stop(s) for s in stops])

        for green_prob, wait, sig in stop_results:
            signal_data = sig
            if sig.get("source") == "learned":
                learned_stops += 1
            else:
                realtime_stops += 1
            green_probs.append(green_prob)
            if green_prob < 0.5:
                red_stops += 1
                total_wait += wait

    if osm_count > 0 and not stops:
        # 버스 정류장 없을 때 OSM 보행 신호등으로 추정 (SE16→Victoria 등)
        red_rate = min(0.55, 0.32 * time_weight)
        red_stops = max(1, round(osm_count * red_rate))
        total_wait = red_stops * 28
        avg_green = 1.0 - (red_stops / osm_count)
        green_probs = [avg_green] * min(osm_count, 6)
    elif osm_count > len(stops) and red_stops == 0:
        # 버스 프록시는 전부 초록인데 OSM상 교차로가 많으면 보정
        extra = osm_count - len(stops)
        supplemental = max(1, round(extra * 0.22 * time_weight))
        red_stops = supplemental
        total_wait += supplemental * 25
        if green_probs:
            green_probs = [max(0.4, p - 0.12) for p in green_probs]

    if green_probs:
        avg_green = sum(green_probs) / len(green_probs)
    elif osm_count > 0:
        avg_green = max(0.45, 1.0 - red_stops / osm_count)
    elif stops:
        avg_green = 0.7
    else:
        return {
            "expected_red_stops": 0,
            "ped_signals_on_path": 0,
            "total_wait_sec": 0,
            "red_probability": 0.3,
            "green_wave_score": 70,
            "stop_count": 0,
            "confidence": 0.1,
            "supabase_learned_stops": 0,
            "supabase_realtime_stops": 0,
            "signal_data_source": "default",
        }

    green_wave_score = round(avg_green * 100, 1)

    return {
        "expected_red_stops": red_stops,
        "ped_signals_on_path": osm_count,
        "total_wait_sec": round(total_wait),
        "red_probability": round(1 - avg_green, 2),
        "green_wave_score": green_wave_score,
        "stop_count": max(len(stops), osm_count),
        "confidence": signal_data.get("confidence", 0.15 if osm_count else 0.1),
        "supabase_learned_stops": learned_stops,
        "supabase_realtime_stops": realtime_stops,
        "signal_data_source": (
            "supabase+realtime" if learned_stops and realtime_stops
            else "supabase" if learned_stops
            else "realtime" if realtime_stops
            else "osm_estimate"
        ),
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
        from app.ingest.signal_collector import get_learned_pattern
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
