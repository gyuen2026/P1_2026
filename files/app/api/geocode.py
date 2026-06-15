"""Address search + reverse geocode (Nominatim proxy for Flutter web CORS)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/geocode", tags=["geocode"])

NOMINATIM = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "LondonRunner/1.0 (running coach; contact@londonrunner.local)"}
# London Zone 1–2 bias
LONDON_VIEWBOX = "-0.25,51.56,0.05,51.46"


@router.get("/search")
async def search_places(q: str, limit: int = 6):
    if not q.strip():
        return {"results": []}
    params = {
        "q": q.strip(),
        "format": "json",
        "limit": min(limit, 10),
        "viewbox": LONDON_VIEWBOX,
        "bounded": "1",
        "addressdetails": "1",
    }
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/search", params=params)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Geocoding service unavailable")
    items = res.json()
    return {
        "results": [
            {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "label": item.get("display_name", ""),
                "name": item.get("name") or item.get("display_name", "").split(",")[0],
            }
            for item in items
        ]
    }


@router.get("/reverse")
async def reverse_geocode(lat: float, lon: float):
    params = {"lat": lat, "lon": lon, "format": "json", "addressdetails": "1"}
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        res = await client.get(f"{NOMINATIM}/reverse", params=params)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Reverse geocoding unavailable")
    item = res.json()
    return {
        "lat": lat,
        "lon": lon,
        "label": item.get("display_name", f"{lat:.5f}, {lon:.5f}"),
        "name": item.get("name") or item.get("display_name", "").split(",")[0],
    }
