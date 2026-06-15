import math
import uuid

from app.services import tfl_service
from app.services.bus_signal_service import calc_route_red_probability
from app.services.fusion_service import predict_signal_at_location
from app.services.signal_prediction import get_london_now, _haversine_km


def calculate_turns(waypoints: list[dict]) -> int:
    """E: Count direction changes > 45° along the path."""
    turns = 0
    if len(waypoints) < 3:
        return 0
    for i in range(1, len(waypoints) - 1):
        p1, p2, p3 = waypoints[i - 1], waypoints[i], waypoints[i + 1]
        b1 = math.atan2(p2["lon"] - p1["lon"], p2["lat"] - p1["lat"])
        b2 = math.atan2(p3["lon"] - p2["lon"], p3["lat"] - p2["lat"])
        angle = abs(math.degrees(b2 - b1))
        if angle > 45:
            turns += 1
    return turns


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
    """Composite score: straighter paths + greener signals − disruptions."""
    turn_penalty = turns * 4
    base = 100 - turn_penalty
    signal_bonus = green_wave_score * 0.4
    penalty = disruption_penalty * 25
    return round(max(0, min(100, base + signal_bonus - penalty)), 1)


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

    scored_routes = []
    for idx, journey in enumerate(journey_data.get("journeys", [])[:5]):
        waypoints = tfl_service.extract_waypoints_from_journey(journey)
        if len(waypoints) < 2:
            continue

        turns = calculate_turns(waypoints)
        distance_km = path_distance_km(waypoints)
        duration_min = round((journey.get("duration") or distance_km * pace_min_per_km * 60) / 60, 1)

        signal_stats = await calc_route_red_probability(waypoints, pace_min_per_km, get_london_now())
        disrupt_pen = await _disruption_penalty_for_path(waypoints, disruptions)
        score = score_route(turns, signal_stats["green_wave_score"], disrupt_pen)

        scored_routes.append({
            "route_id": str(uuid.uuid4()),
            "name": f"Route {idx + 1}",
            "distance_km": distance_km or _target_km,
            "estimated_duration_min": duration_min,
            "signal_stops": signal_stats["expected_red_stops"],
            "signal_wait_total_sec": signal_stats["total_wait_sec"],
            "score": score,
            "turns": turns,
            "green_wave_score": signal_stats["green_wave_score"],
            "polyline": [{"lat": w["lat"], "lon": w["lon"]} for w in waypoints],
            "waypoints": waypoints,
            "description": (
                f"{turns} turns · {signal_stats['green_wave_score']}% green-wave · "
                f"~{signal_stats['expected_red_stops']} signal stops"
            ),
            "status": "clear" if disrupt_pen < 0.3 else "disrupted",
        })

    scored_routes.sort(key=lambda r: r["score"], reverse=True)
    return scored_routes[:5]


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
