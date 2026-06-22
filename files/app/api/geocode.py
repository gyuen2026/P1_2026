"""Fast free geocode — Photon (primary) + optional Google if API key set."""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/geocode", tags=["geocode"])

PHOTON = "https://photon.komoot.io"
NOMINATIM = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "LondonRunner/1.0 (running coach; contact@londonrunner.local)"}
LONDON_CENTER = (51.5074, -0.1278)
LONDON_BBOX = (-0.25, 51.46, 0.05, 51.56)  # min_lon, min_lat, max_lon, max_lat

# In-memory cache — repeated searches feel instant (~0 ms).
_CACHE: OrderedDict[str, tuple[float, list[dict], str]] = OrderedDict()
_CACHE_TTL_S = 300
_CACHE_MAX = 256

AREA_HINTS: dict[str, tuple[float, float]] = {
    "victoria station": (51.4952, -0.1441),
    "victoria": (51.4952, -0.1441),
    "waterloo station": (51.5033, -0.1145),
    "waterloo": (51.5033, -0.1145),
    "kings cross": (51.5308, -0.1238),
    "king's cross": (51.5308, -0.1238),
    "paddington": (51.5154, -0.1755),
    "liverpool street": (51.5178, -0.0813),
    "bank": (51.5133, -0.0886),
    "canary wharf": (51.5050, -0.0230),
    "greenwich": (51.4769, -0.0005),
    "shoreditch": (51.5260, -0.0787),
    "notting hill": (51.5099, -0.1969),
    "camden": (51.5390, -0.1426),
    "westminster": (51.4995, -0.1248),
    "covent garden": (51.5115, -0.1226),
    "soho": (51.5136, -0.1366),
    "brick lane": (51.5215, -0.0718),
    "clapham": (51.4618, -0.1384),
    "brixton": (51.4613, -0.1159),
    "chelsea": (51.4875, -0.1687),
    "islington": (51.5362, -0.1033),
    "stratford": (51.5416, -0.0035),
    "richmond": (51.4613, -0.3037),
    "hampstead": (51.5560, -0.1780),
    "se16": (51.4940, -0.0600),
    "se1": (51.5035, -0.0800),
    "w1": (51.5140, -0.1440),
    "city of london": (51.5155, -0.0922),
}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _is_house_number_only(value: str) -> bool:
    return bool(re.fullmatch(r"[\d\s\-]+", value.strip()))


def _area_bias(q: str) -> tuple[float | None, float | None]:
    lower = q.lower().strip()
    for area, (lat, lon) in sorted(AREA_HINTS.items(), key=lambda x: -len(x[0])):
        if area in lower:
            return lat, lon
    return None, None


def _search_query(raw: str) -> tuple[str, float | None, float | None]:
    """Strip matched area from query so Photon ranks nearby POIs first."""
    lower = raw.lower().strip()
    area_lat: float | None = None
    area_lon: float | None = None
    query = raw.strip()

    for area, (lat, lon) in sorted(AREA_HINTS.items(), key=lambda x: -len(x[0])):
        if area in lower:
            query = re.sub(re.escape(area), "", lower, flags=re.I).strip(" ,")
            area_lat, area_lon = lat, lon
            break

    query = re.sub(r"\b(london|uk|united kingdom)\b", "", query, flags=re.I).strip(" ,")
    if not query:
        query = raw.strip()
    if "london" not in query.lower():
        query = f"{query}, London, UK"
    return query, area_lat, area_lon


def _cache_get(key: str) -> tuple[list[dict], str] | None:
    row = _CACHE.get(key)
    if not row:
        return None
    ts, results, provider = row
    if time.monotonic() - ts > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    _CACHE.move_to_end(key)
    return results, provider


def _cache_set(key: str, results: list[dict], provider: str) -> None:
    _CACHE[key] = (time.monotonic(), results, provider)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def _place_dict(
    *,
    lat: float,
    lon: float,
    label: str,
    name: str,
    category: str = "",
    near_lat: float | None = None,
    near_lon: float | None = None,
) -> dict:
    dist_m = None
    if near_lat is not None and near_lon is not None:
        dist_m = round(_haversine_m(near_lat, near_lon, lat, lon))
    return {
        "lat": lat,
        "lon": lon,
        "label": label,
        "name": name,
        "distance_m": dist_m,
        "category": category,
    }


def _dedupe_places(places: list[dict], precision: int = 4) -> list[dict]:
    seen: set[tuple[float, float]] = set()
    out: list[dict] = []
    for p in places:
        key = (round(p["lat"], precision), round(p["lon"], precision))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _sort_places(places: list[dict]) -> list[dict]:
    return sorted(
        places,
        key=lambda r: r["distance_m"] if r["distance_m"] is not None else 999999,
    )


def _photon_label(props: dict) -> str:
    parts: list[str] = []
    name = (props.get("name") or "").strip()
    if name:
        parts.append(name)
    street_bits = []
    if props.get("housenumber"):
        street_bits.append(str(props["housenumber"]))
    if props.get("street"):
        street_bits.append(str(props["street"]))
    if street_bits:
        parts.append(" ".join(street_bits))
    for key in ("district", "locality", "city", "postcode", "state", "country"):
        val = props.get(key)
        if val and val not in parts:
            parts.append(str(val))
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _photon_title(props: dict) -> str:
    name = (props.get("name") or "").strip()
    if name and not _is_house_number_only(name):
        return name
    if props.get("osm_value"):
        return str(props["osm_value"]).replace("_", " ").title()
    street = props.get("street") or ""
    num = props.get("housenumber") or ""
    if street:
        return f"{num} {street}".strip() if num else street
    city = props.get("city") or props.get("district") or "Location"
    return str(city)


def _in_london(lat: float, lon: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = LONDON_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


async def _photon_search(
    query: str,
    *,
    near_lat: float | None = None,
    near_lon: float | None = None,
    limit: int = 15,
) -> list[dict]:
    """Komoot Photon — free OSM geocoder, typically 200–500 ms."""
    bias_lat = near_lat if near_lat is not None else LONDON_CENTER[0]
    bias_lon = near_lon if near_lon is not None else LONDON_CENTER[1]
    params = {
        "q": query,
        "limit": min(limit, 25),
        "lat": bias_lat,
        "lon": bias_lon,
        "lang": "en",
        "bbox": ",".join(str(v) for v in LONDON_BBOX),
    }
    async with httpx.AsyncClient(timeout=4, headers=HEADERS) as client:
        res = await client.get(f"{PHOTON}/api/", params=params)
    if res.status_code != 200:
        return []

    results: list[dict] = []
    for feat in res.json().get("features") or []:
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not _in_london(lat, lon):
            continue
        props = feat.get("properties") or {}
        results.append(
            _place_dict(
                lat=lat,
                lon=lon,
                label=_photon_label(props),
                name=_photon_title(props),
                category=props.get("osm_value") or props.get("type") or "poi",
                near_lat=near_lat,
                near_lon=near_lon,
            )
        )
    return results


async def _nominatim_search(
    query: str,
    *,
    limit: int = 8,
    near_lat: float | None = None,
    near_lon: float | None = None,
) -> list[dict]:
    params = {
        "q": query,
        "format": "json",
        "limit": limit,
        "viewbox": "-0.25,51.56,0.05,51.46",
        "bounded": "1",
        "addressdetails": "1",
        "dedupe": "1",
    }
    async with httpx.AsyncClient(timeout=3, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/search", params=params)
    if res.status_code != 200:
        return []

    results: list[dict] = []
    for item in res.json():
        lat = float(item["lat"])
        lon = float(item["lon"])
        display = item.get("display_name", "")
        name = (item.get("name") or "").strip()
        if not name or _is_house_number_only(name):
            addr = item.get("address") or {}
            name = addr.get("road") or display.split(",")[0].strip()
        results.append(
            _place_dict(
                lat=lat,
                lon=lon,
                label=display,
                name=name,
                category=item.get("type") or "",
                near_lat=near_lat,
                near_lon=near_lon,
            )
        )
    return results


def _google_api_key() -> str:
    from app.core.config import settings

    return (settings.GOOGLE_MAPS_API_KEY or "").strip()


async def _google_places_search(
    query: str,
    *,
    near_lat: float | None = None,
    near_lon: float | None = None,
    limit: int = 15,
) -> list[dict]:
    key = _google_api_key()
    if not key:
        return []

    bias_lat = near_lat if near_lat is not None else LONDON_CENTER[0]
    bias_lon = near_lon if near_lon is not None else LONDON_CENTER[1]
    text_query = query if "london" in query.lower() else f"{query}, London, UK"
    body = {
        "textQuery": text_query,
        "maxResultCount": min(limit, 20),
        "languageCode": "en",
        "regionCode": "GB",
        "locationBias": {
            "circle": {
                "center": {"latitude": bias_lat, "longitude": bias_lon},
                "radius": 25000.0,
            }
        },
    }
    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": (
            "places.displayName,places.formattedAddress,places.location,places.primaryType"
        ),
    }
    async with httpx.AsyncClient(timeout=5, headers=headers) as client:
        res = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            json=body,
        )
    if res.status_code != 200:
        return []

    results: list[dict] = []
    for place in res.json().get("places") or []:
        loc = place.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            continue
        name = (place.get("displayName") or {}).get("text") or "Location"
        label = place.get("formattedAddress") or name
        results.append(
            _place_dict(
                lat=float(lat),
                lon=float(lon),
                label=label,
                name=name,
                category=place.get("primaryType") or "google_places",
                near_lat=near_lat,
                near_lon=near_lon,
            )
        )
    return results


async def _free_fast_search(
    search_q: str,
    *,
    sort_lat: float,
    sort_lon: float,
    cap: int,
) -> tuple[list[dict], str]:
    """Photon first (~0.3 s); Nominatim only if Photon is sparse."""
    photon = await _photon_search(
        search_q,
        near_lat=sort_lat,
        near_lon=sort_lon,
        limit=cap,
    )
    if len(photon) >= min(3, cap):
        return _sort_places(photon)[:cap], "photon"

    nominatim_task = asyncio.create_task(
        _nominatim_search(
            search_q,
            limit=8,
            near_lat=sort_lat,
            near_lon=sort_lon,
        )
    )
    try:
        extra = await asyncio.wait_for(nominatim_task, timeout=2.0)
    except (asyncio.TimeoutError, Exception):
        nominatim_task.cancel()
        extra = []

    merged = _dedupe_places(photon + extra)
    provider = "photon+nominatim" if extra else "photon"
    return _sort_places(merged)[:cap], provider


@router.get("/search")
async def search_places(
    q: str,
    limit: int = 25,
    near_lat: float | None = None,
    near_lon: float | None = None,
):
    if not q.strip():
        return {"results": []}

    raw = q.strip()
    search_q, area_lat, area_lon = _search_query(raw)
    sort_lat = area_lat or near_lat or LONDON_CENTER[0]
    sort_lon = area_lon or near_lon or LONDON_CENTER[1]
    cap = min(limit, 40)

    cache_key = f"{raw.lower()}|{round(sort_lat, 3)}|{round(sort_lon, 3)}|{cap}"
    cached = _cache_get(cache_key)
    if cached:
        results, provider = cached
        return {"results": results, "provider": provider, "cached": True}

    if _google_api_key():
        google = await _google_places_search(
            search_q,
            near_lat=sort_lat,
            near_lon=sort_lon,
            limit=cap,
        )
        if google:
            results = _sort_places(google)[:cap]
            _cache_set(cache_key, results, "google_places")
            return {"results": results, "provider": "google_places"}

    results, provider = await _free_fast_search(
        search_q,
        sort_lat=sort_lat,
        sort_lon=sort_lon,
        cap=cap,
    )
    _cache_set(cache_key, results, provider)
    return {"results": results, "provider": provider}


@router.get("/reverse")
async def reverse_geocode(lat: float, lon: float):
    params = {"lat": lat, "lon": lon, "lang": "en"}
    async with httpx.AsyncClient(timeout=3, headers=HEADERS) as client:
        res = await client.get(f"{PHOTON}/reverse", params=params)
    if res.status_code == 200:
        feats = res.json().get("features") or []
        if feats:
            props = feats[0].get("properties") or {}
            label = _photon_label(props)
            name = _photon_title(props)
            return {"lat": lat, "lon": lon, "label": label, "name": name}

    params = {"lat": lat, "lon": lon, "format": "json", "addressdetails": "1"}
    async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/reverse", params=params)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Reverse geocoding unavailable")
    item = res.json()
    display = item.get("display_name", f"{lat:.5f}, {lon:.5f}")
    name = (item.get("name") or display.split(",")[0]).strip()
    return {"lat": lat, "lon": lon, "label": display, "name": name}
