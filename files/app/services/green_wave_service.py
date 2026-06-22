"""
Green Wave Commute — 3 variables only (#1 signal accuracy).

Rank 1: max green (~100%) · arrive on time
Ranks 2–5: green targets 95 / 89 / 83 / 77 %
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
    _simplify_waypoints,
    path_distance_km,
)
from app.services.signal_prediction import get_london_now

RANK_GREEN_TARGETS = [100, 95, 89, 83, 77]
MAX_ROUTES = 5


async def _fetch_route_candidates(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    max_routes: int = MAX_ROUTES,
) -> list[tuple[list[dict], float | None]]:
    journey_data = await tfl_service.get_journey_options(start_lat, start_lon, end_lat, end_lon)
    if not journey_data or not journey_data.get("journeys"):
        return []

    seen_sigs: set[tuple[tuple[float, float], ...]] = set()
    raw: list[tuple[list[dict], float | None]] = []

    for journey in journey_data.get("journeys", [])[: max_routes + 2]:
        waypoints = tfl_service.extract_waypoints_from_journey(journey)
        sig = _route_signature(waypoints)
        if len(waypoints) < 2 or sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        duration = float(journey["duration"]) if journey.get("duration") is not None else None
        raw.append((waypoints, duration))
        if len(raw) >= max_routes:
            break

    if len(raw) < max_routes:
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
    """Maximise green % while arriving within available_min (rank #1)."""
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

    for delta in (-0.9, -0.5, -0.2, 0.0, 0.3, 0.6):
        pace = round(baseline + delta, 2)
        if pace < 3.5 or pace > 9.5:
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


async def optimize_pace_for_target_green(
    waypoints: list[dict],
    depart_time: datetime,
    available_min: float,
    target_green: float,
) -> tuple[float, dict]:
    """Tune pace to hit a green % band (ranks 2–5)."""
    slim = _simplify_waypoints(waypoints, max_points=50)
    distance_km = path_distance_km(slim)
    if distance_km < 0.1:
        stats = await calc_route_red_probability(slim, 5.5, depart_time)
        return 5.5, stats

    baseline = available_min / distance_km
    baseline = max(4.0, min(8.5, baseline))

    best_pace = baseline
    best_stats = await calc_route_red_probability(slim, best_pace, depart_time)
    best_diff = abs(best_stats["green_wave_score"] - target_green)

    for delta in (-1.2, -0.8, -0.4, 0.0, 0.4, 0.8, 1.2):
        pace = round(baseline + delta, 2)
        if pace < 3.5 or pace > 9.5:
            continue
        if distance_km * pace > available_min + 1.5:
            continue
        stats = await calc_route_red_probability(slim, pace, depart_time)
        diff = abs(stats["green_wave_score"] - target_green)
        if diff < best_diff:
            best_diff = diff
            best_pace = pace
            best_stats = stats

    return best_pace, best_stats


def _route_payload(
    *,
    slim: list[dict],
    pace: float,
    stats: dict,
    arrive: datetime,
    commute_type: str,
    target_green: float,
) -> dict:
    distance_km = path_distance_km(slim)
    duration_min = round(distance_km * pace, 1)
    depart = arrive - timedelta(minutes=duration_min)
    green = min(100, round(stats["green_wave_score"]))

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
        "score": green,
        "turns": 0,
        "green_wave_score": green,
        "target_green_pct": target_green,
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
    }


def _assign_green_commute_rankings(routes: list[dict]) -> list[dict]:
    labels = [
        ("100% Green Wave", "BEST · 100% GREEN"),
        ("Green Route A", "95% GREEN"),
        ("Green Route B", "89% GREEN"),
        ("Green Route C", "83% GREEN"),
        ("Green Route D", "77% GREEN"),
    ]
    for i, route in enumerate(routes):
        route["rank"] = i + 1
        ped = route.get("ped_signals_on_path", 0)
        pace = route.get("suggested_pace_min_per_km", 0)
        green = route.get("green_wave_score", 0)
        if i == 0:
            route["green_wave_score"] = max(green, min(100, green))
        name, badge = labels[i] if i < len(labels) else (f"Option {i + 1}", f"#{i + 1}")
        route["name"] = name
        route["badge"] = badge
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
    now = get_london_now()
    arrive = now.replace(hour=arrive_hour, minute=arrive_minute, second=0, microsecond=0)
    if arrive <= now:
        arrive += timedelta(days=1)

    available_min = (arrive - now).total_seconds() / 60.0
    if available_min < 5:
        return {"routes": [], "error": "Arrival time too soon — pick a later time"}

    candidates = await _fetch_route_candidates(start_lat, start_lon, end_lat, end_lon)
    if not candidates:
        return {"routes": [], "error": "No routes found"}

    async def _max_green(item: tuple[list[dict], float | None], idx: int):
        wps, _ = item
        if len(wps) < 2:
            return None
        slim = _simplify_waypoints(wps, max_points=50)
        pace, stats = await optimize_pace_for_green(slim, now, available_min)
        return (stats["green_wave_score"], idx, slim, pace, stats)

    max_results = await asyncio.gather(*[_max_green(c, i) for i, c in enumerate(candidates)])
    valid = [r for r in max_results if r is not None]
    if not valid:
        return {"routes": [], "error": "Could not score routes"}

    _, _, slim, pace, stats = max(valid, key=lambda r: r[0])
    routes: list[dict] = []
    routes.append(
        _route_payload(
            slim=slim,
            pace=pace,
            stats=stats,
            arrive=arrive,
            commute_type=commute_type,
            target_green=100,
        )
    )

    used_sigs: set[tuple[tuple[float, float], ...]] = {_route_signature(slim)}

    # Ranks 2–5 — target 95 / 89 / 83 / 77 on alternate paths
    for target in RANK_GREEN_TARGETS[1:]:
        best_alt: tuple[list[dict], float, dict] | None = None
        best_diff = 999.0

        for wps, _ in candidates:
            if len(wps) < 2:
                continue
            slim_alt = _simplify_waypoints(wps, max_points=50)
            sig = _route_signature(slim_alt)
            pace_t, stats_t = await optimize_pace_for_target_green(
                slim_alt, now, available_min, target
            )
            diff = abs(stats_t["green_wave_score"] - target)
            # Prefer unused physical routes, then closest green band
            sig_penalty = 0 if sig not in used_sigs else 8
            score = diff + sig_penalty
            if score < best_diff:
                best_diff = score
                best_alt = (slim_alt, pace_t, stats_t)

        if best_alt:
            slim_alt, pace_t, stats_t = best_alt
            used_sigs.add(_route_signature(slim_alt))
            routes.append(
                _route_payload(
                    slim=slim_alt,
                    pace=pace_t,
                    stats=stats_t,
                    arrive=arrive,
                    commute_type=commute_type,
                    target_green=target,
                )
            )

    routes = _assign_green_commute_rankings(routes[:MAX_ROUTES])

    return {
        "mode": "green_commute",
        "variables": ["route_a_b", "arrive_by_time", "pedestrian_signals"],
        "commute_type": commute_type,
        "arrive_by": arrive.isoformat(),
        "arrive_by_label": arrive.strftime("%H:%M"),
        "minutes_available": round(available_min),
        "green_tiers": RANK_GREEN_TARGETS,
        "routes": routes,
    }
