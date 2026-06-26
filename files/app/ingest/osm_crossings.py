"""
OpenStreetMap traffic-signal crossings for Zone 1-2 (free, Overpass API).
Caches locally to avoid repeated queries.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.predict.signal_prediction import (
    LONDON_LAT_MAX,
    LONDON_LAT_MIN,
    LONDON_LON_MAX,
    LONDON_LON_MIN,
    _haversine_km,
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "osm_crossings.json"
CACHE_TTL_SEC = 7 * 24 * 3600  # refresh weekly
SIGNAL_MATCH_RADIUS_M = 120

_crossings_cache: list[dict] | None = None
_cache_loaded_at: float = 0.0


def _overpass_query() -> str:
    bbox = f"{LONDON_LAT_MIN},{LONDON_LON_MIN},{LONDON_LAT_MAX},{LONDON_LON_MAX}"
    return f"""
[out:json][timeout:90];
(
  node["highway"="traffic_signals"]({bbox});
  node["crossing"="traffic_signals"]({bbox});
);
out body;
"""


def _load_disk_cache() -> list[dict] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(CACHE_PATH.read_text())
        if time.time() - payload.get("fetched_at", 0) > CACHE_TTL_SEC:
            return None
        return payload.get("crossings") or []
    except (json.JSONDecodeError, OSError):
        return None


def _save_disk_cache(crossings: list[dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"fetched_at": time.time(), "count": len(crossings), "crossings": crossings})
    )


async def fetch_crossings_from_overpass() -> list[dict]:
    query = _overpass_query().strip()
    headers = {
        "User-Agent": "LondonRunner/2.1 (signal collection)",
        "Accept": "application/json",
    }
    last_err: Exception | None = None
    data: dict = {}

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for base_url in OVERPASS_ENDPOINTS:
            try:
                res = await client.post(
                    base_url,
                    data={"data": query},
                    headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                )
                if res.status_code == 406:
                    res = await client.get(base_url, params={"data": query}, headers=headers)
                res.raise_for_status()
                data = res.json()
                break
            except Exception as exc:
                last_err = exc
                continue
        else:
            raise last_err or RuntimeError("All Overpass endpoints failed")

    crossings: list[dict] = []
    for el in data.get("elements") or []:
        if el.get("type") != "node":
            continue
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        crossings.append({
            "id": el.get("id"),
            "lat": float(lat),
            "lon": float(lon),
            "highway": tags.get("highway"),
            "crossing": tags.get("crossing"),
        })
    return crossings


def filter_stops_at_signals(
    stops: list[dict],
    crossings: list[dict],
    limit: int | None = None,
) -> list[dict]:
    """Keep stops within SIGNAL_MATCH_RADIUS_M of an OSM traffic signal."""
    from app.predict.signal_prediction import extract_stop_coords, is_valid_london_coord

    matched: list[dict] = []
    for stop in stops:
        lat, lon = extract_stop_coords(stop)
        if not is_valid_london_coord(lat, lon):
            continue
        if is_near_traffic_signal(lat, lon, crossings):
            matched.append(stop)
            if limit and len(matched) >= limit:
                break
    return matched


async def ensure_crossings_loaded(force_refresh: bool = False) -> list[dict]:
    global _crossings_cache, _cache_loaded_at

    if (
        not force_refresh
        and _crossings_cache is not None
        and time.time() - _cache_loaded_at < CACHE_TTL_SEC
    ):
        return _crossings_cache

    disk = None if force_refresh else _load_disk_cache()
    if disk is not None:
        _crossings_cache = disk
        _cache_loaded_at = time.time()
        return _crossings_cache

    try:
        fetched = await fetch_crossings_from_overpass()
        if fetched:
            _save_disk_cache(fetched)
            _crossings_cache = fetched
            _cache_loaded_at = time.time()
            return _crossings_cache
    except Exception as exc:
        print(f"  ⚠️ OSM Overpass fetch failed: {exc}", flush=True)

    # fallback: stale disk or empty
    if CACHE_PATH.exists():
        try:
            payload = json.loads(CACHE_PATH.read_text())
            _crossings_cache = payload.get("crossings") or []
            _cache_loaded_at = time.time()
            return _crossings_cache
        except (json.JSONDecodeError, OSError):
            pass

    _crossings_cache = []
    _cache_loaded_at = time.time()
    print("  ⚠️ OSM crossings unavailable — signal geofence disabled for this cycle", flush=True)
    return _crossings_cache


def nearest_crossing(lat: float, lon: float, crossings: list[dict] | None = None) -> dict | None:
    if crossings is None:
        crossings = _crossings_cache or []
    best = None
    best_m = float("inf")
    for c in crossings:
        d_m = _haversine_km(lat, lon, c["lat"], c["lon"]) * 1000
        if d_m < best_m:
            best_m = d_m
            best = {**c, "distance_m": round(d_m, 1)}
    return best


def is_near_traffic_signal(
    lat: float,
    lon: float,
    crossings: list[dict] | None = None,
    radius_m: float = SIGNAL_MATCH_RADIUS_M,
) -> bool:
    nc = nearest_crossing(lat, lon, crossings)
    return nc is not None and nc["distance_m"] <= radius_m


def osm_confidence_boost(lat: float, lon: float, crossings: list[dict] | None = None) -> float:
    """0–0.15 boost when stop is geofenced to a real OSM traffic signal."""
    nc = nearest_crossing(lat, lon, crossings)
    if not nc:
        return 0.0
    d = nc["distance_m"]
    if d <= 40:
        return 0.15
    if d <= 80:
        return 0.10
    if d <= SIGNAL_MATCH_RADIUS_M:
        return 0.05
    return 0.0


def signals_along_path(
    waypoints: list[dict],
    crossings: list[dict] | None = None,
    path_buffer_m: float = 40,
) -> list[dict]:
    """
    Count distinct OSM pedestrian traffic signals within path_buffer_m of the route.
    More realistic than bus-stop proxy alone (e.g. SE16 → Victoria).
    """
    if crossings is None:
        crossings = _crossings_cache or []
    if not waypoints or not crossings:
        return []

    step = max(1, len(waypoints) // 100)
    samples = waypoints[::step]
    if waypoints[-1] not in samples:
        samples = [*samples, waypoints[-1]]

    matched: list[dict] = []
    seen: set = set()
    for c in crossings:
        cid = c.get("id")
        if cid in seen:
            continue
        min_m = min(
            _haversine_km(c["lat"], c["lon"], wp["lat"], wp["lon"]) * 1000
            for wp in samples
        )
        if min_m <= path_buffer_m:
            seen.add(cid)
            matched.append({**c, "distance_m": round(min_m, 1)})
    return matched
