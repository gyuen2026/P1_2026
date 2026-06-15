import asyncio
import traceback
from collections import defaultdict

from supabase import create_client

from app.core.config import settings
from app.services import tfl_service
from app.services.tfl_service import parse_stops_payload
from app.services.fusion_service import predict_signal_fused
from app.services.osm_crossings import (
    ensure_crossings_loaded,
    filter_stops_at_signals,
    is_near_traffic_signal,
)
from app.services.signal_prediction import (
    build_observation_record,
    calc_delay_detail,
    estimate_delay_confidence,
    extract_stop_coords,
    get_london_now,
    is_valid_london_coord,
    london_hour_and_dow,
)
from app.services.vehicle_signal_service import infer_vehicle_hold, load_bus_positions_for_cycle

BATCH_SIZE = 10
BATCH_DELAY_SEC = 0.5
ARRIVAL_TIMEOUT_SEC = 5.0

_collection_lock = asyncio.Lock()
_collection_state = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_saved": 0,
    "last_skipped": 0,
    "last_error": None,
}


def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


async def get_learned_pattern(stop_id: str, hour: int, dow: int) -> dict | None:
    """Fetch aggregated signal pattern for a stop/time slot."""
    try:
        db = get_supabase()
        result = (
            db.table("signal_patterns")
            .select("*")
            .eq("stop_id", stop_id)
            .eq("hour_of_day", hour)
            .eq("day_of_week", dow)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return None


async def process_and_save_stop(
    stop: dict,
    db,
    disruptions: list[dict],
    *,
    osm_crossings: list[dict] | None = None,
    cycle_vehicles: list[dict] | None = None,
    signal_only: bool = True,
) -> bool:
    """
    Collect G+V+P+N+O via free fusion, insert observation row.
    Returns True if a row was saved.
    """
    lat, lon = extract_stop_coords(stop)
    if not is_valid_london_coord(lat, lon):
        return False

    if signal_only and osm_crossings:
        if not is_near_traffic_signal(lat, lon, osm_crossings):
            return False

    stop_id = stop.get("id") or stop.get("naptanId")
    if not stop_id:
        return False

    now = get_london_now()
    hour, dow = london_hour_and_dow(now)

    learned = await get_learned_pattern(str(stop_id), hour, dow)
    avg_delay, sample_count = 0.0, 0
    delay_methods: list[str] = []
    bus_count = 0
    detail: dict = {}

    try:
        arrivals = await asyncio.wait_for(
            tfl_service.get_bus_arrivals(str(stop_id)),
            timeout=ARRIVAL_TIMEOUT_SEC,
        )
        if isinstance(arrivals, list):
            detail = calc_delay_detail(arrivals, now)
            avg_delay = detail["avg_delay_sec"]
            sample_count = detail["sample_count"]
            delay_methods = detail.get("methods", [])
            bus_count = detail.get("bus_count", 0)
            if delay_methods:
                detail["confidence"] = estimate_delay_confidence(
                    sample_count, delay_methods, bus_count
                )
    except Exception:
        pass

    vehicle_hold = infer_vehicle_hold(lat, lon, cycle_vehicles)

    # Free-tier fusion: G + V + P + N + O (JamCam + crowd on live routes only)
    prediction = await predict_signal_fused(
        lat=lat,
        lon=lon,
        avg_delay_sec=avg_delay,
        sample_count=sample_count,
        delay_detail=detail if sample_count else None,
        learned=learned,
        disruptions=disruptions,
        vehicle_hold=vehicle_hold,
        osm_crossings=osm_crossings,
        hour=hour,
    )
    if delay_methods:
        prediction["sources"]["delay_methods"] = delay_methods

    record = build_observation_record(
        stop,
        avg_delay_sec=avg_delay,
        sample_count=max(sample_count, 1),
        prediction=prediction,
        observed_at=now,
    )
    if not record:
        return False

    try:
        db.table("bus_signal_observations").insert(record).execute()
        return True
    except Exception as exc:
        print(f"  ⚠️ Insert failed for {stop_id}: {exc}", flush=True)
        return False


async def upsert_signal_patterns(db, observations: list[dict]):
    """Aggregate recent observations into signal_patterns."""
    if not observations:
        return

    groups: dict[tuple, list] = defaultdict(list)
    for obs in observations:
        key = (obs["stop_id"], obs["hour_of_day"], obs["day_of_week"])
        groups[key].append(obs)

    for (stop_id, hour, dow), rows in groups.items():
        delays = [r["delay_sec"] for r in rows]
        waits = [r["estimated_wait_sec"] for r in rows]
        cycles = [r["estimated_cycle_sec"] for r in rows]
        count = len(rows)
        avg_delay = sum(delays) / count
        avg_wait = sum(waits) / count
        avg_cycle = sum(cycles) / count
        green_prob = max(0.0, min(1.0, 1.0 - (avg_delay / 60)))

        pattern = {
            "stop_id": stop_id,
            "hour_of_day": hour,
            "day_of_week": dow,
            "avg_delay_sec": round(avg_delay, 1),
            "avg_cycle_sec": round(avg_cycle, 1),
            "avg_wait_sec": round(avg_wait, 1),
            "green_probability": round(green_prob, 3),
            "observation_count": count,
            "last_updated": get_london_now().isoformat(),
        }
        try:
            db.table("signal_patterns").upsert(
                pattern, on_conflict="stop_id,hour_of_day,day_of_week"
            ).execute()
        except Exception as exc:
            print(f"  ⚠️ Pattern upsert failed for {stop_id}: {exc}")


async def run_global_collection(stop_limit: int | None = None):
    db = get_supabase()
    now = get_london_now()
    _collection_state["last_started"] = now.isoformat()
    _collection_state["last_error"] = None
    print(
        f"🌍 [{now.strftime('%H:%M:%S')} London] Zone 1-2 collection "
        f"(dow={now.isoweekday()}, hour={now.hour})"
    )

    disruptions = await tfl_service.get_road_disruptions()

    print("  Loading free-tier sources (OSM + VehiclePositions)...", flush=True)
    osm_crossings = await ensure_crossings_loaded()
    cycle_vehicles = await load_bus_positions_for_cycle()
    print(
        f"  OSM signals: {len(osm_crossings)}, buses tracked: {len(cycle_vehicles)}",
        flush=True,
    )

    try:
        stops_data = await tfl_service.get_all_stops_in_zones(radius=7500)
    except Exception as exc:
        print(f"⚠️ Failed to fetch stops from TfL: {exc}", flush=True)
        traceback.print_exc()
        return

    stops = parse_stops_payload(stops_data)
    use_signal_filter = bool(osm_crossings)

    if stop_limit:
        if use_signal_filter:
            stops = filter_stops_at_signals(stops, osm_crossings, limit=stop_limit)
            print(
                f"🔎 Smoke test: {len(stops)} signal-near stops (OSM geofence)",
                flush=True,
            )
        else:
            stops = stops[:stop_limit]
            print(
                f"🔎 Smoke test: first {len(stops)} stops (no OSM — geofence off)",
                flush=True,
            )
    elif not use_signal_filter:
        print(
            "  ⚠️ Collecting all stops without OSM geofence (Overpass unavailable)",
            flush=True,
        )

    if not stops:
        print("⚠️ No stops to process (check OSM geofence or TfL API).")
        return

    print(f"🔎 Processing {len(stops)} stops (target: 4,000+)...", flush=True)

    saved = 0
    skipped = 0

    for i in range(0, len(stops), BATCH_SIZE):
        chunk = stops[i : i + BATCH_SIZE]
        results = await asyncio.gather(
            *[
                process_and_save_stop(
                    s,
                    db,
                    disruptions,
                    osm_crossings=osm_crossings,
                    cycle_vehicles=cycle_vehicles,
                    signal_only=use_signal_filter,
                )
                for s in chunk
            ]
        )
        batch_saved = sum(1 for r in results if r)
        saved += batch_saved
        skipped += sum(1 for r in results if not r)
        await asyncio.sleep(BATCH_DELAY_SEC)

        if saved == batch_saved and saved <= BATCH_SIZE and batch_saved > 0:
            print(f"  ✅ First rows inserted successfully ({saved} so far)", flush=True)
        if saved == 0 and i >= BATCH_SIZE * 5:
            print(
                "  ❌ Zero inserts after 50 stops — check: Supabase service_role key, "
                "RLS disabled, or OSM geofence (OSM signals: "
                f"{len(osm_crossings or [])}).",
                flush=True,
            )
        if i > 0 and (i // BATCH_SIZE) % 10 == 0:
            print(f"  … {i}/{len(stops)} processed, {saved} saved", flush=True)

    hour, dow = london_hour_and_dow(now)
    try:
        recent = (
            db.table("bus_signal_observations")
            .select("*")
            .eq("hour_of_day", hour)
            .eq("day_of_week", dow)
            .order("observed_at", desc=True)
            .limit(500)
            .execute()
        )
        await upsert_signal_patterns(db, recent.data or [])
    except Exception as exc:
        print(f"⚠️ Pattern refresh failed: {exc}", flush=True)

    print(
        f"✨ Collection complete: {saved} saved, {skipped} skipped.",
        flush=True,
    )
    _collection_state["last_finished"] = get_london_now().isoformat()
    _collection_state["last_saved"] = saved
    _collection_state["last_skipped"] = skipped
    return {"saved": saved, "skipped": skipped}


async def trigger_collection() -> dict:
    """Run one collection cycle (skip if already running)."""
    if _collection_lock.locked():
        return {"status": "already_running", **_collection_state}
    async with _collection_lock:
        _collection_state["running"] = True
        try:
            result = await run_global_collection()
            return {"status": "completed", **result, **_collection_state}
        except Exception as exc:
            _collection_state["last_error"] = str(exc)
            raise
        finally:
            _collection_state["running"] = False


def get_collection_status() -> dict:
    return dict(_collection_state)


async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            async with _collection_lock:
                _collection_state["running"] = True
                try:
                    await run_global_collection()
                finally:
                    _collection_state["running"] = False
        except Exception as e:
            _collection_state["last_error"] = str(e)
            print(f"⚠️ Collection error: {e}", flush=True)
            traceback.print_exc()
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(run_global_collection())
