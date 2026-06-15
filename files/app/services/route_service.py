import math
import uuid

from app.services import tfl_service
from app.services.bus_signal_service import calc_route_red_probability
from app.services.fusion_service import predict_signal_at_location
from app.services.signal_prediction import get_london_now, _haversine_km


def calculate_turns(waypoints: list[dict], angle_threshold: float = 28.0) -> int:
    """E: Count meaningful direction changes along the path."""
    if len(waypoints) < 3:
        return 0
    turns = 0
    prev_bearing: float | None = None
    for i in range(1, len(waypoints)):
        p1, p2 = waypoints[i - 1], waypoints[i]
        seg_m = _haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]) * 1000
        if seg_m < 15:
            continue
        bearing = _bearing_deg(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        if prev_bearing is not None:
            delta = abs(bearing - prev_bearing)
            if delta > 180:
                delta = 360 - delta
            if delta >= angle_threshold:
                turns += 1
        prev_bearing = bearing
    return turns


def compute_runner_score(
    *,
    turns: int,
    green_wave_score: float,
    signal_stops: int,
    signal_wait_sec: int,
    disruption_penalty: float,
) -> float:
    """0–99 composite; spread scores so ranks are meaningful."""
    score = (
        88
        - turns * 5
        - signal_stops * 10
        - min(signal_wait_sec / 30, 15)
        + green_wave_score * 0.25
        - disruption_penalty * 18
    )
    return round(max(12, min(99, score)), 1)


def _assign_route_rankings(routes: list[dict]) -> list[dict]:
    """Sort by score and attach rank + descriptive names."""
    routes.sort(
        key=lambda r: (
            r["score"],
            -r["signal_stops"],
            -r["green_wave_score"],
            -r["turns"],
        ),
        reverse=True,
    )

    if not routes:
        return routes

    fastest = min(routes, key=lambda r: r["estimated_duration_min"])
    greenest = min(routes, key=lambda r: (r["signal_stops"], -r["green_wave_score"]))
    straightest = min(routes, key=lambda r: (r["turns"], r["distance_km"]))

    for i, route in enumerate(routes):
        route["rank"] = i + 1
        tags: list[str] = []
        if route is fastest:
            tags.append("Fastest")
        if route is greenest:
            tags.append("Fewest signals")
        if route is straightest and route["turns"] == straightest["turns"]:
            tags.append("Straightest")

        if i == 0:
            route["name"] = "Recommended"
            route["badge"] = "BEST MATCH"
        elif tags:
            route["name"] = " · ".join(tags)
            route["badge"] = f"#{i + 1}"
        else:
            route["name"] = f"Option {i + 1}"
            route["badge"] = f"#{i + 1}"

        route["description"] = (
            f"{route['turns']} turns · {route['green_wave_score']}% green · "
            f"~{route['signal_stops']} signal stops · "
            f"{route['signal_wait_total_sec']}s wait"
        )
    return routes


def path_distance_km(waypoints: list[dict]) -> float:
    if len(waypoints) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(waypoints)):
        total += _haversine_km(
            waypoints[i - 1]["lat"], waypoints[i - 1]["lon"],
            waypoints[i]["lat"], waypoints[i]["lon"],
        )
    return round(total, 2)


def score_route(turns: int, green_wave_score: float, disruption_penalty: float) -> float:
    """Legacy alias — prefer compute_runner_score."""
    return compute_runner_score(
        turns=turns,
        green_wave_score=green_wave_score,
        signal_stops=0,
        signal_wait_sec=0,
        disruption_penalty=disruption_penalty,
    )


async def _disruption_penalty_for_path(waypoints: list[dict], disruptions: list[dict]) -> float:
    if not waypoints or not disruptions:
        return 0.0
    sample = waypoints[:: max(1, len(waypoints) // 8)]
    hits = 0
    for wp in sample:
        for d in disruptions:
            dlat, dlon = d.get("lat"), d.get("lon")
            if dlat is None or dlon is None:
                continue
            if _haversine_km(wp["lat"], wp["lon"], dlat, dlon) < 0.3:
                hits += 1
                break
    return min(1.0, hits / max(1, len(sample)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.degrees(math.atan2(lon2 - lon1, lat2 - lat1)) % 360


def _interpolate(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float) -> tuple[float, float]:
    f = max(0.0, min(1.0, fraction))
    return lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f


def _move_meters(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Approximate offset — accurate enough for London via-points."""
    br = math.radians(bearing_deg)
    dlat = (distance_m * math.cos(br)) / 111_320
    dlon = (distance_m * math.sin(br)) / (111_320 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def _route_signature(waypoints: list[dict]) -> tuple[tuple[float, float], ...]:
    if len(waypoints) < 2:
        return ()
    picks = [waypoints[0], waypoints[len(waypoints) // 2], waypoints[-1]]
    return tuple((round(p["lat"], 3), round(p["lon"], 3)) for p in picks)


async def _score_waypoints(
    waypoints: list[dict],
    *,
    disruptions: list[dict],
    pace_min_per_km: float,
    target_km: float,
    duration_min: float | None,
    route_index: int,
) -> dict | None:
    if len(waypoints) < 2:
        return None

    turns = calculate_turns(waypoints)
    distance_km = path_distance_km(waypoints)
    est_duration = duration_min if duration_min is not None else round(distance_km * pace_min_per_km, 1)

    signal_stats = await calc_route_red_probability(waypoints, pace_min_per_km, get_london_now())
    disrupt_pen = await _disruption_penalty_for_path(waypoints, disruptions)
    score = compute_runner_score(
        turns=turns,
        green_wave_score=signal_stats["green_wave_score"],
        signal_stops=signal_stats["expected_red_stops"],
        signal_wait_sec=signal_stats["total_wait_sec"],
        disruption_penalty=disrupt_pen,
    )

    return {
        "route_id": str(uuid.uuid4()),
        "name": "Route",
        "badge": "",
        "rank": 0,
        "distance_km": distance_km or target_km,
        "estimated_duration_min": round(est_duration, 1),
        "signal_stops": signal_stats["expected_red_stops"],
        "signal_wait_total_sec": signal_stats["total_wait_sec"],
        "score": score,
        "turns": turns,
        "green_wave_score": signal_stats["green_wave_score"],
        "polyline": [{"lat": w["lat"], "lon": w["lon"]} for w in waypoints],
        "waypoints": waypoints,
        "description": "",
        "status": "clear" if disrupt_pen < 0.3 else "disrupted",
    }


async def _generate_via_alternatives(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    *,
    existing_sigs: set[tuple[tuple[float, float], ...]],
    max_routes: int,
) -> list[tuple[list[dict], float]]:
    """Build extra walking paths by routing through offset via-points."""
    results: list[tuple[list[dict], float]] = []
    base_bearing = _bearing_deg(start_lat, start_lon, end_lat, end_lon)
    side_bearings = ((base_bearing + 90) % 360, (base_bearing - 90) % 360)

    for fraction in (0.35, 0.5, 0.65):
        mid_lat, mid_lon = _interpolate(start_lat, start_lon, end_lat, end_lon, fraction)
        for offset_m in (200, 350, 500):
            for side_bearing in side_bearings:
                if len(results) >= max_routes:
                    return results
                via_lat, via_lon = _move_meters(mid_lat, mid_lon, side_bearing, offset_m)
                chained = await tfl_service.chain_walking_journey(
                    start_lat, start_lon, via_lat, via_lon, end_lat, end_lon,
                )
                if not chained:
                    continue
                waypoints, duration = chained
                sig = _route_signature(waypoints)
                if sig in existing_sigs:
                    continue
                existing_sigs.add(sig)
                results.append((waypoints, duration))
    return results


async def recommend_routes(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    target_pace: float = 5.5,
    target_km: float = 5.0,
    pace: float | None = None,
    dist: float | None = None,
) -> list[dict]:
    """
    I: Return up to 5 routes prioritising minimal turns (E) and green signals (F).
    Accepts both API naming conventions (target_pace/target_km or pace/dist).
    """
    pace_min_per_km = pace if pace is not None else target_pace
    _target_km = dist if dist is not None else target_km

    disruptions = await tfl_service.get_road_disruptions()
    journey_data = await tfl_service.get_journey_options(start_lat, start_lon, end_lat, end_lon)

    if not journey_data or not journey_data.get("journeys"):
        return []

    seen_sigs: set[tuple[tuple[float, float], ...]] = set()
    raw_candidates: list[tuple[list[dict], float | None]] = []

    for journey in journey_data.get("journeys", [])[:5]:
        waypoints = tfl_service.extract_waypoints_from_journey(journey)
        sig = _route_signature(waypoints)
        if len(waypoints) < 2 or sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        duration = float(journey["duration"]) if journey.get("duration") is not None else None
        raw_candidates.append((waypoints, duration))

    if len(raw_candidates) < 5:
        extras = await _generate_via_alternatives(
            start_lat, start_lon, end_lat, end_lon,
            existing_sigs=seen_sigs,
            max_routes=5 - len(raw_candidates),
        )
        raw_candidates.extend(extras)

    scored_routes = []
    for idx, (waypoints, duration) in enumerate(raw_candidates[:8]):
        route = await _score_waypoints(
            waypoints,
            disruptions=disruptions,
            pace_min_per_km=pace_min_per_km,
            target_km=_target_km,
            duration_min=duration,
            route_index=idx,
        )
        if route:
            scored_routes.append(route)

    return _assign_route_rankings(scored_routes)[:5]


def _nearest_disruption(user_lat: float, user_lon: float, disruptions: list[dict]) -> dict | None:
    best = None
    best_dist = float("inf")
    for d in disruptions:
        dlat, dlon = d.get("lat"), d.get("lon")
        if dlat is None or dlon is None:
            continue
        dist = _haversine_km(user_lat, user_lon, dlat, dlon)
        if dist < best_dist:
            best_dist = dist
            best = {**d, "distance_km": dist}
    return best if best and best["distance_km"] < 1.0 else None


def _bearing_to_turn(user_lat: float, user_lon: float, target_lat: float, target_lon: float) -> str:
    """Suggest left/right/straight based on bearing delta."""
    bearing = math.degrees(math.atan2(target_lon - user_lon, target_lat - user_lat)) % 360
    if 45 <= bearing < 135:
        return "east"
    if 135 <= bearing < 225:
        return "south"
    if 225 <= bearing < 315:
        return "west"
    return "north"


def generate_voice_instruction(
    *,
    user_lat: float,
    user_lon: float,
    user_speed_kmh: float,
    heart_rate: int,
    disruptions: list[dict],
    active_waypoints: list[dict] | None = None,
) -> dict:
    """
    Voice guidance engine for dynamic rerouting during a run.
    Monitors J/M (position), L (speed), K (HR), and N (disruptions).
    """
    messages: list[str] = []
    should_reroute = False
    reroute_bearing = "right"
    distance_m = 50

    # K – heart rate coaching
    if heart_rate > 165:
        messages.append(
            f"Heart rate is {heart_rate} bpm. Ease your pace slightly to stay in zone."
        )
    elif heart_rate > 0 and heart_rate < 100 and user_speed_kmh > 12:
        messages.append("Great effort. Your heart rate looks efficient for this pace.")

    # L – pace coaching (pace_min_per_km ≈ 60 / speed_kmh)
    if user_speed_kmh > 0:
        pace_min = 60 / user_speed_kmh
        if pace_min < 4.5:
            messages.append("You're ahead of target pace. Watch for upcoming crossings.")

    # N – accident / disruption rerouting
    nearest = _nearest_disruption(user_lat, user_lon, disruptions)
    if nearest:
        should_reroute = True
        location = nearest.get("location") or "the road ahead"
        category = (nearest.get("category") or "").lower()
        if "accident" in category or "collision" in (nearest.get("comments") or "").lower():
            event = "an accident"
        else:
            event = "a road disruption"

        if active_waypoints and len(active_waypoints) > 2:
            # Detour toward next waypoint after current segment
            target = active_waypoints[min(3, len(active_waypoints) - 1)]
            direction = _bearing_to_turn(user_lat, user_lon, target["lat"], target["lon"])
            reroute_bearing = "right" if direction in ("east", "south") else "left"
            dist_m = int(nearest["distance_km"] * 1000)
            distance_m = max(30, min(100, 100 - dist_m))

        messages.append(
            f"Due to {event} at {location}, the upcoming signal has changed. "
            f"Turn {reroute_bearing} in {distance_m} meters to detour and rejoin your route."
        )
    else:
        messages.append("Your route is clear. Continue on your current path.")

    return {
        "voice_instruction": " ".join(messages),
        "should_reroute": should_reroute,
        "reroute": {
            "turn": reroute_bearing,
            "distance_m": distance_m,
        } if should_reroute else None,
    }


async def check_route_integrity(
    user_lat: float,
    user_lon: float,
    hr: int = 0,
    pace: float = 0,
    speed_kmh: float | None = None,
    route_waypoints: list[dict] | None = None,
) -> dict:
    """
    Real-time route monitor for /routes/check-status.
    pace = min/km; speed_kmh overrides if provided (L).
    """
    disruptions = await tfl_service.get_road_disruptions()
    effective_speed = speed_kmh if speed_kmh else (60 / pace if pace > 0 else 0)

    signal = await predict_signal_at_location(user_lat, user_lon, include_jamcam=True)

    result = generate_voice_instruction(
        user_lat=user_lat,
        user_lon=user_lon,
        user_speed_kmh=effective_speed,
        heart_rate=hr,
        disruptions=disruptions,
        active_waypoints=route_waypoints,
    )

    result["signal"] = {
        "predicted_color": signal["predicted_color"],
        "green_probability": signal["green_probability"],
        "confidence": signal["confidence"],
        "jamcam_check": signal.get("jamcam_check"),
    }
    result["disruptions_nearby"] = len([
        d for d in disruptions
        if d.get("lat") is not None
        and _haversine_km(user_lat, user_lon, d["lat"], d["lon"]) < 0.5
    ])
    return result
