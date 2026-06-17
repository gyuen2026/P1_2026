"""Address search + reverse geocode — Nominatim + Overpass POI (Gail's etc.)."""
from __future__ import annotations

import asyncio
import math
import re

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/geocode", tags=["geocode"])

NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "LondonRunner/1.0 (running coach; contact@londonrunner.local)"}
LONDON_VIEWBOX = "-0.25,51.56,0.05,51.46"
LONDON_BBOX = (51.46, -0.25, 51.56, 0.05)  # south, west, north, east

# Area hints for "gail's victoria" style queries
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


def _extract_brand_and_area(q: str) -> tuple[str, float | None, float | None]:
    lower = q.lower().strip()
    area_lat: float | None = None
    area_lon: float | None = None
    brand = lower

    for area, (lat, lon) in sorted(AREA_HINTS.items(), key=lambda x: -len(x[0])):
        if area in lower:
            brand = lower.replace(area, "").strip(" ,")
            area_lat, area_lon = lat, lon
            break

    brand = re.sub(r"\b(london|uk|united kingdom)\b", "", brand, flags=re.I).strip(" ,")
    return brand, area_lat, area_lon


def _brand_keyword(brand: str) -> str | None:
    """gail's → gail for Overpass name~ regex."""
    cleaned = re.sub(r"[^a-zA-Z0-9']", " ", brand).strip()
    if len(cleaned) < 2:
        return None
    token = cleaned.split()[0].replace("'", "")
    return token[:14] if len(token) >= 3 else None


def _build_address_from_tags(tags: dict) -> str:
    parts: list[str] = []
    name = tags.get("name")
    if name:
        parts.append(name)
    street_bits = []
    if tags.get("addr:housenumber"):
        street_bits.append(str(tags["addr:housenumber"]))
    if tags.get("addr:street"):
        street_bits.append(str(tags["addr:street"]))
    if street_bits:
        parts.append(" ".join(street_bits))
    if tags.get("addr:suburb"):
        parts.append(str(tags["addr:suburb"]))
    elif tags.get("addr:neighbourhood"):
        parts.append(str(tags["addr:neighbourhood"]))
    if tags.get("addr:postcode"):
        parts.append(str(tags["addr:postcode"]))
    parts.append("London, United Kingdom")
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _title_from_nominatim(item: dict) -> str:
    name = (item.get("name") or "").strip()
    addr = item.get("address") or {}
    if name and not _is_house_number_only(name):
        return name
    if addr.get("amenity"):
        return str(addr["amenity"]).replace("_", " ").title()
    if addr.get("shop"):
        return str(addr["shop"]).replace("_", " ").title()
    road = addr.get("road", "")
    num = addr.get("house_number", "")
    if road:
        return f"{num} {road}".strip() if num else road
    display = item.get("display_name", "")
    return display.split(",")[0].strip() if display else "Location"


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


async def _overpass_poi_search(
    keyword: str,
    *,
    limit: int = 50,
    area_lat: float | None = None,
    area_lon: float | None = None,
    area_only: bool = False,
    near_lat: float | None = None,
    near_lon: float | None = None,
) -> list[dict]:
    if area_only and area_lat is not None and area_lon is not None:
        d_lat, d_lon = 0.022, 0.035
        south, north = area_lat - d_lat, area_lat + d_lat
        west, east = area_lon - d_lon, area_lon + d_lon
    else:
        south, west, north, east = LONDON_BBOX

    # Case-insensitive partial name match (Gail, Gail's Bakery, etc.)
    query = f"""
    [out:json][timeout:30];
    (
      nwr["name"~"{keyword}",i]({south},{west},{north},{east});
    );
    out center {min(limit, 60)};
    """
    async with httpx.AsyncClient(timeout=35, headers=HEADERS) as client:
        res = await client.post(OVERPASS, data={"data": query})
    if res.status_code != 200:
        return []

    elements = res.json().get("elements") or []
    results: list[dict] = []
    sort_lat = area_lat or near_lat
    sort_lon = area_lon or near_lon

    for el in elements:
        tags = el.get("tags") or {}
        poi_name = tags.get("name")
        if not poi_name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        label = _build_address_from_tags(tags)
        results.append(
            _place_dict(
                lat=float(lat),
                lon=float(lon),
                label=label,
                name=poi_name,
                category=tags.get("amenity") or tags.get("shop") or "poi",
                near_lat=sort_lat,
                near_lon=sort_lon,
            )
        )
    return results


async def _nominatim_search(
    query: str,
    *,
    limit: int = 15,
    near_lat: float | None = None,
    near_lon: float | None = None,
    viewbox: str = LONDON_VIEWBOX,
) -> list[dict]:
    params = {
        "q": query,
        "format": "json",
        "limit": limit,
        "viewbox": viewbox,
        "bounded": "1",
        "addressdetails": "1",
        "dedupe": "1",
    }
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/search", params=params)
    if res.status_code != 200:
        return []

    results: list[dict] = []
    for item in res.json():
        lat = float(item["lat"])
        lon = float(item["lon"])
        display = item.get("display_name", "")
        results.append(
            _place_dict(
                lat=lat,
                lon=lon,
                label=display,
                name=_title_from_nominatim(item),
                category=item.get("type") or item.get("class") or "",
                near_lat=near_lat,
                near_lon=near_lon,
            )
        )
    return results


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
    brand, area_lat, area_lon = _extract_brand_and_area(raw)
    keyword = _brand_keyword(brand)
    sort_lat = area_lat or near_lat
    sort_lon = area_lon or near_lon
    cap = min(limit, 40)

    tasks: list = []
    if keyword:
        tasks.append(
            _overpass_poi_search(
                keyword,
                limit=50,
                area_lat=area_lat,
                area_lon=area_lon,
                area_only=area_lat is not None,
                near_lat=sort_lat,
                near_lon=sort_lon,
            )
        )

    nominatim_q = raw if "london" in raw.lower() else f"{raw}, London, UK"
    tasks.append(
        _nominatim_search(
            nominatim_q,
            limit=15,
            near_lat=sort_lat,
            near_lon=sort_lon,
        )
    )

    batches = await asyncio.gather(*tasks, return_exceptions=True)
    merged: list[dict] = []
    for batch in batches:
        if isinstance(batch, list):
            merged.extend(batch)

    merged = _dedupe_places(merged)

    if sort_lat is not None and sort_lon is not None:
        merged.sort(key=lambda r: r["distance_m"] if r["distance_m"] is not None else 999999)

    return {"results": merged[:cap]}


@router.get("/reverse")
async def reverse_geocode(lat: float, lon: float):
    params = {"lat": lat, "lon": lon, "format": "json", "addressdetails": "1"}
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/reverse", params=params)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Reverse geocoding unavailable")
    item = res.json()
    display = item.get("display_name", f"{lat:.5f}, {lon:.5f}")
    return {
        "lat": lat,
        "lon": lon,
        "label": display,
        "name": _title_from_nominatim(item),
    }
