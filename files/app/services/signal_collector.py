import asyncio
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_stop_data(stop, db):
    stop_id = stop.get("id")
    arrivals = await tfl_service.get_bus_arrivals(stop_id)
    if not arrivals or not isinstance(arrivals, list): return

    delays = []
    for bus in arrivals:
        if isinstance(bus, dict):
            exp, sch = bus.get("expectedArrival"), bus.get("scheduledArrival")
            if exp and sch:
                d = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - 
                     datetime.fromisoformat(sch.replace("Z", "+00:00"))).total_seconds()
                delays.append(d)
    
    if delays:
        avg_delay = sum(delays) / len(delays)
        record = {
            "stop_id": stop_id, "stop_name": stop.get("commonName"),
            "lat": stop.get("lat"), "lon": stop.get("lon"),
            "delay_sec": round(avg_delay, 1),
            "predicted_color": "RED" if avg_delay > 20 else "GREEN",
            "observed_at": datetime.now(timezone.utc).isoformat()
        }
        db.table("bus_signal_observations").insert(record).execute()

async def run_global_collection():
    db = get_supabase()
    print("🌍 런던 1-3존 광역 수집 시작...")
    stops_data = await tfl_service.get_all_stops_in_zones()
    stops = stops_data.get("stopPoints", []) if stops_data else []
    
    # 50개씩 끊어서 병렬 처리 (API 제한 준수)
    chunk_size = 50
    for i in range(0, len(stops), chunk_size):
        chunk = stops[i:i+chunk_size]
        await asyncio.gather(*[process_stop_data(s, db) for s in chunk])
        await asyncio.sleep(1.5) 
    print(f"✅ {len(stops)}개 정류장 처리 완료")

async def run_scheduler(interval_minutes: int = 30):
    print(f"🚀 스케줄러 가동: {interval_minutes}분 간격")
    while True:
        try: await run_global_collection()
        except Exception as e: print(f"❌ 수집 에러: {e}")
        await asyncio.sleep(interval_minutes * 60)
