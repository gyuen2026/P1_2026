import asyncio
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_and_save_stop(stop, db):
    """정류장 정보를 가져와 즉시 DB에 저장하고 로그를 남김"""
    stop_id = stop.get("id")
    try:
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
                "stop_id": stop_id, 
                "stop_name": stop.get("commonName"),
                "lat": stop.get("lat"), 
                "lon": stop.get("lon"),
                "delay_sec": round(avg_delay, 1),
                "predicted_color": "RED" if avg_delay > 20 else "GREEN",
                "observed_at": datetime.now(timezone.utc).isoformat()
            }
            # 즉시 저장
            db.table("bus_signal_observations").insert(record).execute()
            print(f"  [Row 삽입] {stop.get('commonName')} (지연: {round(avg_delay, 1)}초)")
    except Exception as e:
        # 에러 발생 시 로그만 찍고 다음 정류장으로 진행
        print(f"  [Row 실패] {stop_id}: {e}")

async def run_global_collection():
    db = get_supabase()
    print(f"🌍 [{datetime.now().strftime('%H:%M:%S')}] 1-3존 광역 수집 프로세스 가동...")
    
    stops_data = await tfl_service.get_all_stops_in_zones()
    stops = stops_data.get("stopPoints", []) if stops_data else []
    print(f"🔎 총 {len(stops)}개의 정류장을 찾았습니다. 순차적으로 저장 시작...")

    # API 과부하를 막기 위해 10개씩 조심스럽게 처리
    chunk_size = 10
    for i in range(0, len(stops), chunk_size):
        chunk = stops[i:i+chunk_size]
        await asyncio.gather(*[process_and_save_stop(s, db) for s in chunk])
        # 1초당 10개씩 저장 (TfL과 Supabase 부하 분산)
        await asyncio.sleep(1.0) 

async def run_scheduler(interval_minutes: int = 30):
    print(f"🚀 스케줄러가 {interval_minutes}분 간격으로 대기 중입니다.")
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"❌ 스케줄러 치명적 에러: {e}")
        await asyncio.sleep(interval_minutes * 60)
