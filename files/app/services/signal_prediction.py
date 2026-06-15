"""
Unified signal prediction: combines bus delays (G), JamCam (H),
accident/disruption data (N), and learned patterns to predict signal state (F).
"""
from __future__ import annotations

import asyncio
import io
import json
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from PIL import Image

LONDON_TZ = ZoneInfo("Europe/London")

# London Zone 1-2 approximate bounds for validation
LONDON_LAT_MIN, LONDON_LAT_MAX = 51.45, 51.58
LONDON_LON_MIN, LONDON_LON_MAX = -0.25, 0.08


def get_london_now() -> datetime:
    """London local time (BST/GMT via IANA timezone)."""
    return datetime.now(LONDON_TZ)


def london_hour_and_dow(dt: datetime | None = None) -> tuple[int, int]:
    """Return (hour_of_day 0-23, day_of_week 1=Mon..7=Sun)."""
    now = dt or get_london_now()
    local = now.astimezone(LONDON_TZ)
    return local.hour, local.isoweekday()


def is_valid_london_coord(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if lat_f == 0.0 and lon_f == 0.0:
        return False
    return (
        LONDON_LAT_MIN <= lat_f <= LONDON_LAT_MAX
        and LONDON_LON_MIN <= lon_f <= LONDON_LON_MAX
    )


def extract_stop_coords(stop: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from a TfL StopPoint, checking common field variants."""
    lat = stop.get("lat")
    lon = stop.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)

    centroid = stop.get("centrePoint") or stop.get("centre")
    if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
        return float(centroid[1]), float(centroid[0])

    for child in stop.get("children") or []:
        clat, clon = extract_stop_coords(child)
        if is_valid_london_coord(clat, clon):
            return clat, clon

    return None, None


def resolve_stop_name(stop: dict) -> str:
    for key in ("commonName", "fullName", "indicator", "id", "naptanId"):
        val = stop.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return "Unknown Stop"


def _parse_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _prediction_sent_at(bus: dict) -> datetime | None:
    timing = bus.get("timing") or {}
    return _parse_utc(timing.get("sent") or timing.get("read") or bus.get("timestamp"))


def calc_delay_from_arrivals(
    arrivals: list[dict],
    observed_at: datetime | None = None,
) -> tuple[float, int]:
    """
    Estimate corridor delay / signal-hold proxy from TfL StopPoint/Arrivals.

    TfL no longer returns scheduledArrival on most predictions. Methods used:
      1) Legacy: expectedArrival − scheduledArrival (if both exist)
      2) Drift: prediction age vs timeToStation mismatch (stale/hold)
      3) Bunching: 2+ buses within 75s TTS gap → queue at signals
    """
    result = calc_delay_detail(arrivals, observed_at)
    return result["avg_delay_sec"], result["sample_count"]


def calc_delay_detail(
    arrivals: list[dict],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Full delay breakdown with confidence for logging and accuracy scoring."""
    now = observed_at or datetime.now(LONDON_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=LONDON_TZ)
    now_utc = now.astimezone(ZoneInfo("UTC"))

    delays: list[float] = []
    methods: list[str] = []

    valid = [b for b in arrivals if isinstance(b, dict)]

    # Method 1 — legacy scheduled vs expected
    for bus in valid:
        exp, sch = bus.get("expectedArrival"), bus.get("scheduledArrival") or bus.get(
            "scheduledArrivalTime"
        )
        if not exp or not sch:
            continue
        exp_t, sch_t = _parse_utc(exp), _parse_utc(sch)
        if not exp_t or not sch_t:
            continue
        delay = (exp_t - sch_t).total_seconds()
        if -30 < delay < 300:
            delays.append(delay)
            methods.append("scheduled")

    # Method 2 — prediction drift (recent predictions only)
    for bus in valid:
        tts = bus.get("timeToStation")
        exp_t = _parse_utc(bus.get("expectedArrival"))
        sent_t = _prediction_sent_at(bus)
        if tts is None or exp_t is None or sent_t is None:
            continue
        if not (30 <= tts <= 600):
            continue
        age = (now_utc - sent_t.astimezone(ZoneInfo("UTC"))).total_seconds()
        if age > 90 or age < 0:
            continue
        remaining = (exp_t - now_utc).total_seconds()
        drift = remaining - tts
        if 8 < drift < 180:
            delays.append(drift)
            methods.append("drift")
        elif drift <= -15:
            # Bus ahead of countdown — slight negative, treat as minimal delay
            delays.append(max(0.0, 5 + drift * 0.3))
            methods.append("drift_early")

    # Method 3 — bus bunching near stop (signal queue proxy)
    near = [
        b for b in valid
        if b.get("timeToStation") is not None and 45 <= b["timeToStation"] <= 420
    ]
    if len(near) >= 2:
        tts_sorted = sorted(b["timeToStation"] for b in near)
        min_gap = min(tts_sorted[i + 1] - tts_sorted[i] for i in range(len(tts_sorted) - 1))
        if min_gap < 75:
            bunch_delay = min(90.0, max(12.0, 75 - min_gap + 10))
            delays.append(bunch_delay)
            methods.append("bunching")

    # Method 4 — lead bus moderate TTS with low movement (single-bus hold hint)
    if not delays:
        imminent = [b for b in valid if b.get("timeToStation") is not None and 90 <= b["timeToStation"] <= 240]
        if imminent:
            lead_tts = min(b["timeToStation"] for b in imminent)
            hold_proxy = max(0.0, min(45.0, (lead_tts - 90) * 0.35))
            if hold_proxy >= 10:
                delays.append(hold_proxy)
                methods.append("lead_hold")

    if not delays:
        return {
            "avg_delay_sec": 0.0,
            "sample_count": 0,
            "confidence": 0.1,
            "methods": [],
            "bus_count": len(valid),
        }

    avg = sum(delays) / len(delays)
    confidence = estimate_delay_confidence(len(delays), methods, len(valid))

    return {
        "avg_delay_sec": round(avg, 1),
        "sample_count": len(delays),
        "confidence": confidence,
        "methods": methods,
        "bus_count": len(valid),
    }


def estimate_delay_confidence(sample_count: int, methods: list[str], bus_count: int) -> float:
    """
    Heuristic confidence (0–1) for delay estimate quality.
    Calibrated against TfL proxy limitations (no C-ITS ground truth).
    """
    base = min(0.55, 0.15 + sample_count * 0.08)
    if "scheduled" in methods:
        base += 0.25
    if "bunching" in methods:
        base += 0.12
    if "drift" in methods:
        base += 0.10
    if bus_count >= 3:
        base += 0.05
    return round(min(0.85, base), 2)


def estimate_system_accuracy() -> dict[str, Any]:
    """Legacy wrapper — delegates to free-tier fusion accuracy doc."""
    from app.services.fusion_service import estimate_free_tier_accuracy
    return estimate_free_tier_accuracy()


def _road_roi(gray: np.ndarray) -> np.ndarray:
    """Lower portion of frame where the carriageway / crossing typically appears."""
    h = gray.shape[0]
    return gray[int(h * 0.55) :, :]


def _movement_score(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    """
    0 = static scene (queued traffic, cars stopped at red → pedestrian GREEN likely)
    1 = high movement (cars flowing → pedestrian RED likely)
    """
    roi_a = _road_roi(frame_a).astype(np.float32)
    roi_b = _road_roi(frame_b).astype(np.float32)
    if roi_a.shape != roi_b.shape:
        return 0.5
    diff = np.abs(roi_a - roi_b)
    return float(np.clip(diff.mean() / 28.0, 0.0, 1.0))


def infer_pedestrian_color_from_jamcam_frames(
    frame_a: np.ndarray, frame_b: np.ndarray | None = None
) -> tuple[str, float]:
    """
    H: Infer pedestrian signal from JamCam (free CV — motion + queue variance).
    Queued/stopped traffic → pedestrian GREEN; flowing traffic → pedestrian RED.
    """
    move = _movement_score(frame_a, frame_b) if frame_b is not None else 0.5

    roi = _road_roi(frame_a)
    static_score = 1.0 - float(np.clip(roi.std() / 42.0, 0.0, 1.0))

    if frame_b is not None:
        roi_b = _road_roi(frame_b)
        edge_motion = float(
            np.abs(roi.astype(np.float32) - roi_b.astype(np.float32)).mean() / 22.0
        )
        move = max(move, min(1.0, edge_motion))

    queue_score = (1.0 - move) * 0.6 + static_score * 0.4

    if queue_score >= 0.58:
        return "GREEN", round(min(0.88, 0.55 + queue_score * 0.35), 2)
    if move >= 0.52:
        return "RED", round(min(0.85, 0.5 + move * 0.35), 2)
    return "AMBER", 0.45


async def _fetch_jamcam_frame(url: str) -> np.ndarray | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            res = await client.get(url)
            if res.status_code != 200:
                return None
            img = Image.open(io.BytesIO(res.content)).convert("L")
            img = img.resize((160, 120))
            return np.array(img)
    except Exception:
        return None


async def jamcam_double_check_pedestrian_signal(
    jamcams: list[dict],
    bus_prediction: dict[str, Any],
) -> dict[str, Any]:
    """
    H: Double-check the bus-derived pedestrian signal colour using JamCam imagery.

    Does NOT drive the initial prediction — only confirms or challenges it.
    """
    if not jamcams:
        return {
            "verified": False,
            "jamcam_color": None,
            "jamcam_confidence": 0.0,
            "agrees_with_bus": None,
            "jamcam_url": None,
            "note": "No JamCam in range",
        }

    nearest = min(
        (c for c in jamcams if c.get("imageUrl")),
        key=lambda c: c.get("distance", 0),
        default=None,
    )
    if not nearest:
        nearest = next((c for c in jamcams if c.get("imageUrl")), None)
    if not nearest or not nearest.get("imageUrl"):
        return {
            "verified": False,
            "jamcam_color": None,
            "jamcam_confidence": 0.0,
            "agrees_with_bus": None,
            "jamcam_url": None,
            "note": "JamCam found but no imageUrl",
        }

    url = nearest["imageUrl"]
    frame_a = await _fetch_jamcam_frame(url)
    if frame_a is None:
        return {
            "verified": False,
            "jamcam_color": None,
            "jamcam_confidence": 0.0,
            "agrees_with_bus": None,
            "jamcam_url": url,
            "note": "Could not fetch JamCam image",
        }

    await asyncio.sleep(1.2)
    frame_b = await _fetch_jamcam_frame(url)

    jam_color, jam_conf = infer_pedestrian_color_from_jamcam_frames(frame_a, frame_b)
    bus_color = bus_prediction["predicted_color"]
    agrees = jam_color == bus_color or (
        jam_color == "AMBER" or bus_color == "AMBER"
    )

    return {
        "verified": True,
        "jamcam_color": jam_color,
        "jamcam_confidence": round(jam_conf, 2),
        "agrees_with_bus": agrees,
        "jamcam_url": url,
        "jamcam_id": nearest.get("id"),
        "note": "Confirmed by JamCam" if agrees else "JamCam disagrees with bus estimate",
    }


def merge_jamcam_verification(
    bus_prediction: dict[str, Any],
    jamcam_check: dict[str, Any],
) -> dict[str, Any]:
    """Apply H double-check result onto the G-based prediction."""
    merged = dict(bus_prediction)
    merged["sources"] = dict(bus_prediction.get("sources", {}))
    merged["sources"]["jamcam_verified"] = jamcam_check.get("verified", False)
    merged["sources"]["jamcam_url"] = jamcam_check.get("jamcam_url")

    if not jamcam_check.get("verified"):
        merged["jamcam_check"] = jamcam_check
        return merged

    jam_color = jamcam_check["jamcam_color"]
    jam_conf = jamcam_check["jamcam_confidence"]
    agrees = jamcam_check["agrees_with_bus"]
    bus_color = bus_prediction["predicted_color"]

    merged["jamcam_check"] = jamcam_check

    if agrees:
        merged["confidence"] = round(min(1.0, merged["confidence"] + jam_conf * 0.15), 2)
        merged["sources"]["jamcam_confirms"] = bus_color
    else:
        # JamCam overrides when it is more confident than the bus estimate
        if jam_conf > merged["confidence"]:
            merged["predicted_color"] = jam_color
            if jam_color == "GREEN":
                merged["green_probability"] = round(jam_conf, 3)
            elif jam_color == "RED":
                merged["green_probability"] = round(1.0 - jam_conf, 3)
            else:
                merged["green_probability"] = 0.5
            merged["red_probability"] = round(1.0 - merged["green_probability"], 3)
            merged["confidence"] = round(jam_conf * 0.9, 2)
            merged["sources"]["jamcam_override"] = jam_color
        else:
            merged["confidence"] = round(max(0.1, merged["confidence"] - 0.2), 2)
            merged["sources"]["jamcam_conflict"] = jam_color

    return merged


def disruption_proximity_score(
    lat: float, lon: float, disruptions: list[dict], radius_km: float = 0.5
) -> float:
    """N: 0=none nearby, 1=accident directly on location."""
    if not disruptions:
        return 0.0
    min_dist = float("inf")
    for d in disruptions:
        dlat, dlon = d.get("lat"), d.get("lon")
        if dlat is None or dlon is None:
            continue
        dist = _haversine_km(lat, lon, dlat, dlon)
        min_dist = min(min_dist, dist)
    if min_dist == float("inf"):
        return 0.0
    if min_dist <= radius_km * 0.2:
        return 1.0
    if min_dist <= radius_km:
        return 1.0 - (min_dist / radius_km)
    return 0.0


def predict_signal_state(
    *,
    avg_delay_sec: float = 0.0,
    sample_count: int = 0,
    disruptions: list[dict] | None = None,
    lat: float = 0.0,
    lon: float = 0.0,
    learned: dict | None = None,
    hour: int | None = None,
    jamcams: list[dict] | None = None,  # ignored — use predict_signal_state_with_jamcam
) -> dict[str, Any]:
    """
    F (primary): Predict pedestrian signal from bus delays (G), learned patterns,
    and disruptions (N). Pass jamcams to predict_signal_state_with_jamcam for H.
    """
    disruptions = disruptions or []
    hour = hour if hour is not None else get_london_now().hour

    # G – bus delay component
    if sample_count > 0:
        signal_wait = abs(avg_delay_sec) * 0.7
        cycle_sec = min(max(signal_wait * 2.8, 45), 150)
        bus_red_score = min(1.0, max(0.0, avg_delay_sec / 45))
        bus_confidence = min(1.0, sample_count / 5)
    elif learned and learned.get("observation_count", 0) >= 3:
        signal_wait = learned.get("avg_wait_sec", 30)
        cycle_sec = learned.get("avg_cycle_sec", 90)
        bus_red_score = 1.0 - learned.get("green_probability", 0.5)
        bus_confidence = min(0.9, learned.get("observation_count", 0) / 20)
    else:
        signal_wait = 30.0
        cycle_sec = 90.0
        bus_red_score = 0.4
        bus_confidence = 0.15

    # N – nearby accidents increase red probability
    disrupt_score = disruption_proximity_score(lat, lon, disruptions)
    disrupt_red_boost = disrupt_score * 0.45

    rush_factor = 1.3 if hour in (7, 8, 9, 17, 18, 19) else 1.0

    red_probability = min(
        1.0,
        (bus_red_score * 0.55 + disrupt_red_boost) * rush_factor,
    )
    green_probability = round(1.0 - red_probability, 3)

    if green_probability >= 0.55:
        predicted_color = "GREEN"
    elif green_probability <= 0.35:
        predicted_color = "RED"
    else:
        predicted_color = "AMBER"

    confidence = round(min(1.0, bus_confidence * 0.7 + disrupt_score * 0.1), 2)

    return {
        "predicted_color": predicted_color,
        "green_probability": green_probability,
        "red_probability": round(red_probability, 3),
        "estimated_wait_sec": round(signal_wait, 1),
        "estimated_cycle_sec": round(cycle_sec, 1),
        "confidence": confidence,
        "sources": {
            "bus_delay_sec": round(avg_delay_sec, 1),
            "bus_sample_count": sample_count,
            "disruption_nearby": disrupt_score > 0.2,
            "learned": bool(learned),
            "primary_source": "bus_delay",
        },
    }


async def predict_signal_state_with_jamcam(
    *,
    jamcams: list[dict] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Full F pipeline: G+N prediction, then H double-check via JamCam imagery."""
    bus_prediction = predict_signal_state(**kwargs)
    jamcam_check = await jamcam_double_check_pedestrian_signal(jamcams or [], bus_prediction)
    return merge_jamcam_verification(bus_prediction, jamcam_check)


def build_observation_record(
    stop: dict,
    *,
    avg_delay_sec: float,
    sample_count: int,
    prediction: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Build a Supabase row with zero NULLs for required fields.
    Returns None if stop coordinates are invalid.
    """
    lat, lon = extract_stop_coords(stop)
    if not is_valid_london_coord(lat, lon):
        return None

    stop_id = stop.get("id") or stop.get("naptanId")
    if not stop_id:
        return None

    stop_name = resolve_stop_name(stop)
    now = observed_at or get_london_now()
    hour, dow = london_hour_and_dow(now)

    return {
        "stop_id": str(stop_id),
        "stop_name": stop_name,
        "lat": float(lat),
        "lon": float(lon),
        "hour_of_day": hour,
        "day_of_week": dow,
        "delay_sec": round(float(avg_delay_sec), 1),
        "estimated_cycle_sec": float(prediction["estimated_cycle_sec"]),
        "estimated_wait_sec": float(prediction["estimated_wait_sec"]),
        "sample_count": max(1, int(sample_count)),
        "observed_at": now.isoformat(),
    }


def parse_disruption_point(point) -> tuple[float | None, float | None]:
    """
    Parse TfL road disruption location.
    TfL returns point as '[lon, lat]' JSON array string, not a GeoJSON Point object.
    """
    if point is None:
        return None, None

    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return float(point[1]), float(point[0])

    if isinstance(point, dict):
        coords = point.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[1]), float(coords[0])
        return None, None

    if not isinstance(point, str):
        return None, None

    try:
        geo = json.loads(point)
        if isinstance(geo, list) and len(geo) >= 2:
            return float(geo[1]), float(geo[0])
        if isinstance(geo, dict):
            coords = geo.get("coordinates") or []
            if len(coords) >= 2:
                return float(coords[1]), float(coords[0])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None, None


def normalize_disruptions(raw: list | None) -> list[dict]:
    """Normalize TfL road disruption payloads with lat/lon and location name."""
    if not raw or not isinstance(raw, list):
        return []
    results = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        lat, lon = parse_disruption_point(d.get("point"))
        if lat is None:
            lat, lon = d.get("lat"), d.get("lon")
        results.append({
            "id": d.get("id"),
            "location": d.get("location") or str(d.get("comments") or "")[:80],
            "category": d.get("category"),
            "severity": d.get("severity"),
            "status": d.get("status"),
            "lat": lat,
            "lon": lon,
            "comments": d.get("comments"),
            "currentUpdate": d.get("currentUpdate"),
        })
    return results


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))
