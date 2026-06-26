import json

import asyncio
import traceback

import httpx

from app.core.config import settings
from app.predict.signal_prediction import normalize_disruptions

TFL_BASE = "https://api.tfl.gov.uk"
LONDON_CENTER = (51.5074, -0.1278)
ZONE_12_RADIUS_M = 7500

# Geo queries cover Zone 1-2 better as overlapping tiles (no `page` param — it 404s).
LONDON_GRID = [
    (51.5074, -0.1278),
    (51.5230, -0.1050),
    (51.4920, -0.1520),
    (51.5150, -0.0750),
    (51.4980, -0.0950),
]


def _is_tfl_error(data) -> bool:
    return isinstance(data, dict) and bool(data.get("httpStatusCode"))


async def get_tfl_data(endpoint: str, params: dict | None = None, timeout: float = 30):
    if params is None:
        params = {}
    params["app_key"] = settings.TFL_APP_KEY
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            res = await client.get(f"{TFL_BASE}{endpoint}", params=params)
            if res.status_code != 200:
                return None
            return res.json()
        except Exception:
            return None


def _extract_stop_points(data) -> tuple[list[dict], int | None]:
    """TfL StopPoint responses may be a dict or a raw list."""
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)], len(data)
    if isinstance(data, dict):
        if _is_tfl_error(data):
            return [], None
        batch = data.get("stopPoints") or data.get("StopPoints") or []
        if not isinstance(batch, list):
            batch = []
        total = data.get("total") or data.get("$totalStops")
        return batch, total
    return [], None


def parse_stops_payload(stops_data) -> list[dict]:
    """Safely extract stop dicts from any TfL payload shape."""
    batch, _ = _extract_stop_points(stops_data)
    return batch


async def _fetch_stops_at(lat: float, lon: float, radius: int) -> list[dict]:
    """Single geo StopPoint query — never pass `page` (breaks this endpoint)."""
    data = await get_tfl_data(
        "/StopPoint",
        {
            "lat": lat,
            "lon": lon,
            "radius": radius,
            "stopTypes": "NaptanPublicBusCoachTram",
        },
        timeout=90,
    )
    if not data or _is_tfl_error(data):
        return []
    batch, _ = _extract_stop_points(data)
    return batch


async def get_all_stops_in_zones(
    lat: float = LONDON_CENTER[0],
    lon: float = LONDON_CENTER[1],
    radius: int = ZONE_12_RADIUS_M,
) -> dict:
    """
    Fetch bus stops across London Zones 1-2 using overlapping geo tiles.
    """
    all_stops: list[dict] = []
    seen: set[str] = set()
    tile_radius = min(radius, 4000)

    centers = LONDON_GRID if (lat, lon) == LONDON_CENTER else [(lat, lon)]

    for center_lat, center_lon in centers:
        batch = await _fetch_stops_at(center_lat, center_lon, tile_radius)
        for stop in batch:
            sid = stop.get("id")
            if sid and sid not in seen:
                seen.add(sid)
                all_stops.append(stop)
        await asyncio.sleep(0.3)

    print(f"  TfL: {len(all_stops)} unique stops from {len(centers)} tiles", flush=True)
    return {"stopPoints": all_stops, "total": len(all_stops)}


async def get_bus_arrivals(stop_id: str):
    """G. Real-time bus delay data for a stop."""
    return await get_tfl_data(f"/StopPoint/{stop_id}/Arrivals")


async def get_road_disruptions() -> list[dict]:
    """N. Live road accident/disruption data with parsed coordinates."""
    try:
        data = await get_tfl_data("/Road/All/Disruption")
        if isinstance(data, list):
            return normalize_disruptions(data)
        if isinstance(data, dict):
            if _is_tfl_error(data):
                return []
            items = data.get("disruptions") or data.get("Disruptions")
            if isinstance(items, list):
                return normalize_disruptions(items)
            return []
        return []
    except Exception as exc:
        print(f"  ⚠️ Road disruption fetch failed: {exc}", flush=True)
        return []


async def get_nearby_jamcams(lat: float, lon: float, radius: int = 500) -> list[dict]:
    """H. Live JamCam CCTV URLs near a coordinate."""
    data = await get_tfl_data("/Place", {
        "type": "JamCam",
        "lat": lat,
        "lon": lon,
        "radius": radius,
    })
    if not data or isinstance(data, str):
        return []
    places = data if isinstance(data, list) else data.get("places") or []
    if not isinstance(places, list):
        return []
    return [{
        "id": c.get("id"),
        "imageUrl": next(
            (p["value"] for p in (c.get("additionalProperties") or []) if p.get("key") == "imageUrl"),
            None,
        ),
        "lat": c.get("lat"),
        "lon": c.get("lon"),
        "commonName": c.get("commonName"),
    } for c in places if isinstance(c, dict)]


async def get_journey_options(start_lat: float, start_lon: float, end_lat: float, end_lon: float):
    """Walking journey candidates between two points."""
    from_loc = f"{start_lat},{start_lon}"
    to_loc = f"{end_lat},{end_lon}"
    data = await get_tfl_data(
        f"/Journey/JourneyResults/{from_loc}/to/{to_loc}",
        {"mode": "walking", "alternativeCycle": "true"},
    )
    if isinstance(data, dict):
        return data
    return {}


async def chain_walking_journey(
    start_lat: float,
    start_lon: float,
    via_lat: float,
    via_lon: float,
    end_lat: float,
    end_lon: float,
) -> tuple[list[dict], float] | None:
    """Start → via → end walking path (TfL rarely returns >2 direct alternatives)."""
    leg1 = await get_journey_options(start_lat, start_lon, via_lat, via_lon)
    leg2 = await get_journey_options(via_lat, via_lon, end_lat, end_lon)
    j1 = (leg1.get("journeys") or [None])[0]
    j2 = (leg2.get("journeys") or [None])[0]
    if not j1 or not j2:
        return None

    waypoints: list[dict] = []
    for journey in (j1, j2):
        waypoints.extend(extract_waypoints_from_journey(journey))

    deduped: list[dict] = []
    for wp in waypoints:
        if not deduped or deduped[-1] != wp:
            deduped.append(wp)
    if len(deduped) < 2:
        return None

    duration = float(j1.get("duration") or 0) + float(j2.get("duration") or 0)
    return deduped, duration


def decode_line_string(line_string: str) -> list[dict]:
    """Decode TfL path lineString into waypoint dicts."""
    if not line_string:
        return []

    stripped = line_string.strip()

    # TfL walking paths: JSON array [[lat, lon], ...]
    if stripped.startswith("["):
        try:
            coords = json.loads(stripped)
            waypoints = []
            for pair in coords:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    waypoints.append({"lat": float(pair[0]), "lon": float(pair[1])})
            return waypoints
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # GeoJSON-style: space-separated lon lat pairs
    waypoints = []
    parts = stripped.split()
    for i in range(0, len(parts) - 1, 2):
        try:
            lon, lat = float(parts[i]), float(parts[i + 1])
            waypoints.append({"lat": lat, "lon": lon})
        except (ValueError, IndexError):
            continue
    return waypoints


def extract_waypoints_from_journey(journey: dict) -> list[dict]:
    """Extract polyline coordinates from a TfL walking journey."""
    if not isinstance(journey, dict):
        return []
    waypoints: list[dict] = []
    for leg in journey.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        path = leg.get("path") or {}
        line_string = path.get("lineString") or ""
        waypoints.extend(decode_line_string(line_string))

        for pt_key in ("departurePoint", "arrivalPoint"):
            pt = leg.get(pt_key) or {}
            if not isinstance(pt, dict):
                continue
            plat, plon = pt.get("lat"), pt.get("lon")
            if plat is not None and plon is not None:
                coord = {"lat": float(plat), "lon": float(plon)}
                if not waypoints or waypoints[-1] != coord:
                    waypoints.append(coord)

    deduped: list[dict] = []
    for wp in waypoints:
        if not deduped or deduped[-1] != wp:
            deduped.append(wp)
    return deduped
