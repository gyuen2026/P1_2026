import asyncio
from collections import defaultdict

from supabase import create_client

from app.core.config import settings
from app.services import tfl_service
from app.services.signal_prediction import (
    build_observation_record,
    calc_delay_from_arrivals,
    get_london_now,
    london_hour_and_dow,
    predict_signal_state_with_jamcam,
)

BATCH_SIZE = 10
BATCH_DELAY_SEC = 0.5
ARRIVAL_TIMEOUT_SEC = 5.0


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


async def process_and_save_stop(stop: dict, db, disruptions: list[dict]) -> bool:
    """
    Collect G+H, predict F, and insert a fully populated observation row.
    Returns True if a row was saved.
    """
    from app.services.signal_prediction import extract_stop_coords, is_valid_london_coord

    lat, lon = extract_stop_coords(stop)
    if not is_valid_london_coord(lat, lon):
        return False

    stop_id = stop.get("id") or stop.get("naptanId")
    if not stop_id:
        return False

    now = get_london_now()
    hour, dow = london_hour_and_dow(now)

    learned = await get_learned_pattern(str(stop_id), hour, dow)
    avg_delay, sample_count = 0.0, 0

    try:
        arrivals = await asyncio.wait_for(
            tfl_service.get_bus_arrivals(str(stop_id)),
            timeout=ARRIVAL_TIMEOUT_SEC,
        )
        if isinstance(arrivals, list):
            avg_delay, sample_count = calc_delay_from_arrivals(arrivals)
    except Exception:
        pass

    jamcams = await tfl_service.get_nearby_jamcams(lat, lon, radius=400)

    prediction = await predict_signal_state_with_jamcam(
        avg_delay_sec=avg_delay,
        sample_count=sample_count,
        jamcams=jamcams,
        disruptions=disruptions,
        lat=lat,
        lon=lon,
        learned=learned,
        hour=hour,
    )

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
        print(f"  ⚠️ Insert failed for {stop_id}: {exc}")
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


async def run_global_collection():
    db = get_supabase()
    now = get_london_now()
    print(
        f"🌍 [{now.strftime('%H:%M:%S')} London] Zone 1-2 collection "
        f"(dow={now.isoweekday()}, hour={now.hour})"
    )

    disruptions = await tfl_service.get_road_disruptions()
    stops_data = await tfl_service.get_all_stops_in_zones(radius=7500)
    stops = stops_data.get("stopPoints", []) if stops_data else []

    if not stops:
        print("⚠️ No stops returned from TfL API.")
        return

    print(f"🔎 Processing {len(stops)} stops (target: 4,000+)...")

    saved = 0
    skipped = 0
    batch_records: list[dict] = []

    for i in range(0, len(stops), BATCH_SIZE):
        chunk = stops[i : i + BATCH_SIZE]
        results = await asyncio.gather(
            *[process_and_save_stop(s, db, disruptions) for s in chunk]
        )
        saved += sum(1 for r in results if r)
        skipped += sum(1 for r in results if not r)
        await asyncio.sleep(BATCH_DELAY_SEC)

        if (i // BATCH_SIZE) % 20 == 0 and i > 0:
            print(f"  … {i}/{len(stops)} processed, {saved} saved")

    # Refresh patterns from latest hour's data
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
        batch_records = recent.data or []
        await upsert_signal_patterns(db, batch_records)
    except Exception as exc:
        print(f"⚠️ Pattern refresh failed: {exc}")

    print(f"✨ Collection complete: {saved} saved, {skipped} skipped (invalid coords).")


async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"⚠️ Collection error: {e}")
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(run_global_collection())
