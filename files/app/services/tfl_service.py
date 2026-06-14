import asyncio
import httpx
from app.core.config import settings
from app.services.signal_prediction import normalize_disruptions

TFL_BASE = "https://api.tfl.gov.uk"
LONDON_CENTER = (51.5074, -0.1278)
ZONE_12_RADIUS_M = 7500


async def get_tfl_data(endpoint: str, params: dict | None = None):
    if params is None:
        params = {}
    params["app_key"] = settings.TFL_APP_KEY
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            res = await client.get(f"{TFL_BASE}{endpoint}", params=params)
            if res.status_code != 200:
                return None
            return res.json()
        except Exception:
            return None


def _extract_stop_points(data) -> tuple[list[dict], int | None]:
    """TfL StopPoint responses may be a dict or a raw list depending on endpoint/params."""
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)], len(data)
    if isinstance(data, dict):
        batch = data.get("stopPoints") or data.get("StopPoints") or []
        if not isinstance(batch, list):
            batch = []
        total = data.get("total") or data.get("$totalStops")
        return batch, total
    return [], None


async def get_all_stops_in_zones(
    lat: float = LONDON_CENTER[0],
    lon: float = LONDON_CENTER[1],
    radius: int = ZONE_12_RADIUS_M,
) -> dict:
    """
    Fetch all bus stops in London Zones 1-2 (~7.5 km radius).
    Paginates until every stop is retrieved (~4,000+).
    """
    all_stops: list[dict] = []
    seen: set[str] = set()
    page = 1

    while True:
        data = await get_tfl_data("/StopPoint", {
            "lat": lat,
            "lon": lon,
            "radius": radius,
            "stopTypes": "NaptanPublicBusCoachTram",
            "page": page,
        })
        if not data:
            break

        batch, total = _extract_stop_points(data)
        for stop in batch:
            sid = stop.get("id")
            if sid and sid not in seen:
                seen.add(sid)
                all_stops.append(stop)

        if not batch:
            break
        if total is not None and len(all_stops) >= total:
            break
        # Geo search often returns everything on page 1 with no further pages
        if isinstance(data, list) or page > 1 and len(batch) == 0:
            break
        page += 1
        await asyncio.sleep(0.2)
        if page > 50:
            break

    return {"stopPoints": all_stops, "total": len(all_stops)}


async def get_bus_arrivals(stop_id: str):
    """G. Real-time bus delay data for a stop."""
    return await get_tfl_data(f"/StopPoint/{stop_id}/Arrivals")


async def get_road_disruptions() -> list[dict]:
    """N. Live road accident/disruption data with parsed coordinates."""
    data = await get_tfl_data("/Road/All/Disruption")
    if isinstance(data, list):
        return normalize_disruptions(data)
    if isinstance(data, dict):
        items = data.get("disruptions") or data.get("Disruptions") or []
        return normalize_disruptions(items if isinstance(items, list) else [])
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
    return [{
        "id": c.get("id"),
        "imageUrl": next(
            (p["value"] for p in c.get("additionalProperties", []) if p.get("key") == "imageUrl"),
            None,
        ),
        "lat": c.get("lat"),
        "lon": c.get("lon"),
        "commonName": c.get("commonName"),
    } for c in data if isinstance(c, dict)]


async def get_journey_options(start_lat: float, start_lon: float, end_lat: float, end_lon: float):
    """Walking journey candidates between two points."""
    from_loc = f"{start_lat},{start_lon}"
    to_loc = f"{end_lat},{end_lon}"
    return await get_tfl_data(
        f"/Journey/JourneyResults/{from_loc}/to/{to_loc}",
        {"mode": "walking", "alternativeCycle": "true"},
    )


def decode_line_string(line_string: str) -> list[dict]:
    """Decode TfL GeoJSON lineString (lon lat pairs) into waypoint dicts."""
    if not line_string:
        return []
    waypoints = []
    parts = line_string.strip().split()
    for i in range(0, len(parts) - 1, 2):
        try:
            lon, lat = float(parts[i]), float(parts[i + 1])
            waypoints.append({"lat": lat, "lon": lon})
        except (ValueError, IndexError):
            continue
    return waypoints


def extract_waypoints_from_journey(journey: dict) -> list[dict]:
    """Extract polyline coordinates from a TfL walking journey."""
    waypoints: list[dict] = []
    for leg in journey.get("legs") or []:
        path = leg.get("path") or {}
        line_string = path.get("lineString") or ""
        waypoints.extend(decode_line_string(line_string))

        for pt_key in ("departurePoint", "arrivalPoint"):
            pt = leg.get(pt_key) or {}
            lat, lon = pt.get("lat"), pt.get("lon")
            if lat is not None and lon is not None:
                coord = {"lat": float(lat), "lon": float(lon)}
                if not waypoints or waypoints[-1] != coord:
                    waypoints.append(coord)

    # Deduplicate consecutive identical points
    deduped: list[dict] = []
    for wp in waypoints:
        if not deduped or deduped[-1] != wp:
            deduped.append(wp)
    return deduped
