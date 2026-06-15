"""
Crowd-sourced signal reports (free) — runner taps GREEN/RED at crossings.
Stored in Supabase; 2+ agreeing reports within 5 min override fusion.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.signal_prediction import _haversine_km, get_london_now

LONDON = ZoneInfo("Europe/London")
CONSENSUS_RADIUS_M = 60
CONSENSUS_WINDOW_MIN = 5
MIN_REPORTS = 2

# In-memory fallback when Supabase table missing
_memory_reports: list[dict] = []


def _get_db():
    from supabase import create_client
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


async def submit_report(
    *,
    lat: float,
    lon: float,
    reported_color: str,
    waited_sec: float = 0.0,
    stop_id: str | None = None,
) -> dict:
    color = reported_color.upper()
    if color not in ("GREEN", "RED", "AMBER", "WAITING"):
        color = "AMBER"

    now = get_london_now()
    row = {
        "lat": float(lat),
        "lon": float(lon),
        "reported_color": color,
        "waited_sec": float(waited_sec),
        "stop_id": stop_id,
        "reported_at": now.isoformat(),
    }

    try:
        db = _get_db()
        db.table("crowd_signal_reports").insert(row).execute()
    except Exception:
        _memory_reports.append(row)
        if len(_memory_reports) > 500:
            _memory_reports[:] = _memory_reports[-500:]

    consensus = await get_consensus_near(lat, lon)
    return {"status": "saved", "consensus": consensus}


async def get_consensus_near(lat: float, lon: float) -> dict[str, Any]:
    cutoff = get_london_now() - timedelta(minutes=CONSENSUS_WINDOW_MIN)
    reports: list[dict] = []

    try:
        db = _get_db()
        result = (
            db.table("crowd_signal_reports")
            .select("*")
            .gte("reported_at", cutoff.isoformat())
            .order("reported_at", desc=True)
            .limit(100)
            .execute()
        )
        reports = result.data or []
    except Exception:
        reports = [
            r for r in _memory_reports
            if _parse_ts(r.get("reported_at")) >= cutoff
        ]

    nearby = []
    for r in reports:
        rlat, rlon = r.get("lat"), r.get("lon")
        if rlat is None or rlon is None:
            continue
        d_m = _haversine_km(lat, lon, rlat, rlon) * 1000
        if d_m <= CONSENSUS_RADIUS_M:
            nearby.append({**r, "distance_m": round(d_m, 1)})

    if len(nearby) < MIN_REPORTS:
        return {
            "report_count": len(nearby),
            "consensus_color": None,
            "confidence": 0.0,
            "avg_wait_sec": 0.0,
        }

    colors = [r["reported_color"] for r in nearby if r.get("reported_color")]
    waits = [r.get("waited_sec") or 0 for r in nearby]
    green_n = sum(1 for c in colors if c == "GREEN")
    red_n = sum(1 for c in colors if c == "RED")
    total = len(colors)

    if green_n >= red_n and green_n >= MIN_REPORTS:
        consensus = "GREEN"
        agree = green_n / total
    elif red_n > green_n and red_n >= MIN_REPORTS:
        consensus = "RED"
        agree = red_n / total
    else:
        consensus = "AMBER"
        agree = max(green_n, red_n) / max(total, 1)

    conf = min(0.95, 0.5 + agree * 0.35 + (len(nearby) - MIN_REPORTS) * 0.05)

    return {
        "report_count": len(nearby),
        "consensus_color": consensus,
        "confidence": round(conf, 2),
        "avg_wait_sec": round(sum(waits) / len(waits), 1) if waits else 0.0,
        "green_votes": green_n,
        "red_votes": red_n,
    }


def _parse_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.min.replace(tzinfo=LONDON)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=LONDON)
