"""
Green Wave Commute — 3 variables only (#1 signal accuracy).

  1. Route A→B (start/end coordinates)
  2. Arrive-by time (London)
  3. Pedestrian signal timing (OSM + bus + crowd fusion)

Pace is an OUTPUT (reverse-suggested), not an input.
No manual distance, pace, JamCam, or accident weighting in ranking.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from app.services import tfl_service
from app.services.bus_signal_service import calc_route_red_probability
from app.services.route_service import (
    _generate_via_alternatives,
    _route_signature,
    _signal_wait_tier,
    _simplify_waypoints,
    path_distance_km,
)
from app.services.signal_prediction import get_london_now


async def _fetch_route_candidates(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    max_routes: int = 3,
) -> list[tuple[list[dict], float | None]]:
    journey_data = await tfl_service.get_journey_options(start_lat, start_lon, end_lat, end_lon)
    if not journey_data or not journey_data.get("journeys"):
        return []

    seen_sigs: set[tuple[tuple[float, float], ...]] = set()
    raw: list[tuple[list[dict], float | None]] = []

    for journey in journey_data.get("journeys", [])[:max_routes]:
        waypoints = tfl_service.extract_waypoints_from_journey(journey)
        sig = _route_signature(waypoints)
        if len(waypoints) < 2 or sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        duration = float(journey["duration"]) if journey.get("duration") is not None else None
        raw.append((waypoints, duration))

    if len(raw) < 2:
        extras = await _generate_via_alternatives(
            start_lat, start_lon, end_lat, end_lon,
            existing_sigs=seen_sigs,
            max_routes=max(0, max_routes - len(raw)),
        )
        raw.extend(extras)

    return raw[:max_routes]


async def optimize_pace_for_green(
    waypoints: list[dict],
    depart_time: datetime,
    available_min: float,
) -> tuple[float, dict]:
    """
    Find pace (min/km) that arrives within available_min while maximising green_wave_score.
    """
    slim = _simplify_waypoints(waypoints, max_points=50)
    distance_km = path_distance_km(slim)
    if distance_km < 0.1:
        stats = await calc_route_red_probability(slim, 5.5, depart_time)
        return 5.5, stats

    baseline = available_min / distance_km
    baseline = max(4.0, min(8.5, baseline))

    best_pace = baseline
    best_stats = await calc_route_red_probability(slim, best_pace, depart_time)
    best_green = best_stats["green_wave_score"]

    for delta in (-0.6, 0.0, 0.6):
        pace = round(baseline + delta, 2)
        if pace < 3.8 or pace > 9.0:
            continue
        if distance_km * pace > available_min + 1.5:
            continue
        stats = await calc_route_red_probability(slim, pace, depart_time)
        green = stats["green_wave_score"]
        if green > best_green or (
            green == best_green and stats["expected_red_stops"] < best_stats["expected_red_stops"]
        ):
            best_green = green
            best_pace = pace
            best_stats = stats

    return best_pace, best_stats


def _assign_green_commute_rankings(routes: list[dict]) -> list[dict]:
    routes.sort(
        key=lambda r: (
            -r["green_wave_score"],
            _signal_wait_tier(r["signal_stops"]),
            r["signal_stops"],
        ),
    )
    for i, route in enumerate(routes):
        route["rank"] = i + 1
        ped = route.get("ped_signals_on_path", 0)
        pace = route.get("suggested_pace_min_per_km", 0)
        if i == 0:
            route["name"] = "Green Wave Commute"
            route["badge"] = "BEST GREEN"
        else:
            route["name"] = f"Green option {i + 1}"
            route["badge"] = f"#{i + 1}"
        route["description"] = (
            f"Run {pace:.1f} min/km · {route['green_wave_score']}% green · "
            f"{ped} signals · ~{route['signal_stops']} red · "
            f"arrive {route.get('arrive_by_label', '')}"
        )
    return routes


async def recommend_green_commute(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    arrive_hour: int,
    arrive_minute: int,
    *,
    commute_type: str = "work",
) -> dict:
    """
    Commute mode: home↔office, arrive by HH:MM London, pace reverse-suggested for greens.
    """
    now = get_london_now()
    tz = now.tzinfo
    arrive = now.replace(hour=arrive_hour, minute=arrive_minute, second=0, microsecond=0)
    if arrive <= now:
        arrive += timedelta(days=1)

    available_min = (arrive - now).total_seconds() / 60.0
    if available_min < 5:
        return {"routes": [], "error": "Arrival time too soon — pick a later time"}

    candidates = await _fetch_route_candidates(start_lat, start_lon, end_lat, end_lon)

    async def _score_commute(item: tuple[list[dict], float | None]) -> dict | None:
        wps, _dur = item
        if len(wps) < 2:
            return None
        slim = _simplify_waypoints(wps, max_points=50)
        pace, stats = await optimize_pace_for_green(slim, now, available_min)
        distance_km = path_distance_km(slim)
        duration_min = round(distance_km * pace, 1)
        depart = arrive - timedelta(minutes=duration_min)

        return {
            "route_id": str(uuid.uuid4()),
            "name": "Commute",
            "badge": "",
            "rank": 0,
            "distance_km": distance_km,
            "estimated_duration_min": duration_min,
            "signal_stops": stats["expected_red_stops"],
            "ped_signals_on_path": stats.get("ped_signals_on_path", 0),
            "signal_wait_total_sec": stats["total_wait_sec"],
            "score": stats["green_wave_score"],
            "turns": 0,
            "green_wave_score": stats["green_wave_score"],
            "polyline": [{"lat": w["lat"], "lon": w["lon"]} for w in slim],
            "waypoints": slim,
            "description": "",
            "status": "clear",
            "mode": "green_commute",
            "commute_type": commute_type,
            "suggested_pace_min_per_km": round(pace, 2),
            "depart_at": depart.isoformat(),
            "arrive_by": arrive.isoformat(),
            "arrive_by_label": arrive.strftime("%H:%M"),
            "depart_at_label": depart.strftime("%H:%M"),
            "minutes_until_arrive": round(available_min),
        }

    scored = await asyncio.gather(*[_score_commute(c) for c in candidates])
    routes = _assign_green_commute_rankings([r for r in scored if r])[:3]

    return {
        "mode": "green_commute",
        "variables": ["route_a_b", "arrive_by_time", "pedestrian_signals"],
        "commute_type": commute_type,
        "arrive_by": arrive.isoformat(),
        "arrive_by_label": arrive.strftime("%H:%M"),
        "minutes_available": round(available_min),
        "routes": routes,
    }
