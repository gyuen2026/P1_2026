"""
TfL Vehicle Positions — free bulk fetch once per collection cycle.
Infers signal hold / queue from approaching buses near a stop.
"""
from __future__ import annotations

from typing import Any

from app.services import tfl_service
from app.services.signal_prediction import _haversine_km

_cycle_vehicles: list[dict] | None = None


async def load_bus_positions_for_cycle() -> list[dict]:
    """
    Fetch bus GPS for the cycle. TfL /Vehicle/VehiclePositions often 404 on
    standard keys — returns [] gracefully; per-stop Arrivals still power fusion.
    """
    global _cycle_vehicles
    _cycle_vehicles = []
    for endpoint in ("/Vehicle/VehiclePositions/bus", "/Vehicle/VehiclePositions/Bus"):
        data = await tfl_service.get_tfl_data(endpoint, timeout=30)
        if isinstance(data, list) and data:
            break
    else:
        return _cycle_vehicles

    vehicles = []
    for v in data:
        if not isinstance(v, dict):
            continue
        loc = v.get("location") or v.get("currentLocation") or {}
        lat = loc.get("lat") if isinstance(loc, dict) else None
        lon = loc.get("lon") if isinstance(loc, dict) else None
        if lat is None or lon is None:
            continue
        vehicles.append({
            "vehicleId": v.get("vehicleId"),
            "lineId": v.get("lineId") or v.get("lineName"),
            "lat": float(lat),
            "lon": float(lon),
            "bearing": v.get("bearing"),
            "timeToStation": v.get("timeToStation"),
            "towards": v.get("towards"),
        })
    _cycle_vehicles = vehicles
    return vehicles


def get_cycle_vehicles() -> list[dict]:
    return _cycle_vehicles or []


def vehicles_near_stop(
    stop_lat: float,
    stop_lon: float,
    vehicles: list[dict] | None = None,
    radius_km: float = 0.35,
) -> list[dict]:
    pool = vehicles if vehicles is not None else get_cycle_vehicles()
    out = []
    for v in pool:
        d = _haversine_km(stop_lat, stop_lon, v["lat"], v["lon"])
        if d <= radius_km:
            out.append({**v, "distance_km": round(d, 3)})
    return out


def infer_vehicle_hold(
    stop_lat: float,
    stop_lon: float,
    vehicles: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Estimate corridor hold from nearby bus GPS + timeToStation.
    Returns green_prob, confidence, wait_sec for fusion layer.
    """
    near = vehicles_near_stop(stop_lat, stop_lon, vehicles)
    if not near:
        return {
            "green_probability": 0.5,
            "confidence": 0.0,
            "estimated_wait_sec": 30.0,
            "estimated_cycle_sec": 90.0,
            "vehicle_count": 0,
            "hold_score": 0.0,
        }

    tts_vals = [
        v["timeToStation"]
        for v in near
        if v.get("timeToStation") is not None and 45 <= v["timeToStation"] <= 420
    ]

    hold_score = 0.0
    wait_sec = 25.0

    if len(tts_vals) >= 2:
        tts_sorted = sorted(tts_vals)
        min_gap = min(tts_sorted[i + 1] - tts_sorted[i] for i in range(len(tts_sorted) - 1))
        if min_gap < 80:
            hold_score = min(1.0, (80 - min_gap) / 80 + 0.2)
            wait_sec = min(90.0, max(15.0, 80 - min_gap + 12))
    elif len(tts_vals) == 1:
        t = tts_vals[0]
        if 120 <= t <= 240:
            hold_score = 0.35
            wait_sec = (t - 90) * 0.4

    # high hold → pedestrian RED likely (vehicles queued)
    red_prob = min(1.0, hold_score * 0.85 + 0.15)
    green_prob = 1.0 - red_prob
    conf = min(0.78, 0.25 + len(near) * 0.08 + hold_score * 0.25)

    return {
        "green_probability": round(green_prob, 3),
        "confidence": round(conf, 2),
        "estimated_wait_sec": round(wait_sec, 1),
        "estimated_cycle_sec": round(min(150, max(45, wait_sec * 2.8)), 1),
        "vehicle_count": len(near),
        "hold_score": round(hold_score, 2),
    }
