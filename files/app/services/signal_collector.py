import asyncio
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_and_save_stop(stop, db):
    """정류장별 데이터 수집 및 즉시 저장"""
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
            # 즉시 저장 시도
            db.table("bus_signal_observations").insert(record).execute()
            print(f"  [DB 성공] {stop.get('commonName')}")
    except Exception as e:
        print(f"  [DB 실패] {stop_id}: {str(e)[:50]}")

async def run_global_collection():
    db = get_supabase()
    print(f"🚀 [{datetime.now().strftime('%H:%M:%S')}] 수집 엔진 가동 (Zone 1 집중)")
    
    # 1. 연결 테스트용 데이터 무조건 한 개 삽입
    try:
        db.table("bus_signal_observations").insert({
            "stop_id": "STSTEM_CHECK", "stop_name": "Server Heartbeat",
            "lat": 51.5, "lon": -0.1, "delay_sec": 0, "predicted_color": "GREEN",
            "observed_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        print("✅ [DB 연결 확인] 테스트 데이터 삽입 완료!")
    except Exception as e:
        print(f"❌ [DB 연결 오류] Supabase가 거부함: {e}")

    # 2. 수집 범위를 2km(Zone 1)로 좁혀서 안정성 확보
    stops_data = await tfl_service.get_all_stops_in_zones(radius=2000)
    stops = stops_data.get("stopPoints", []) if stops_data else []
    print(f"🔎 중심부 정류장 {len(stops)}개 발견. 저장 시작...")

    for i in range(0, len(stops), 5): # 5개씩 천천히 처리
        chunk = stops[i:i+5]
        await asyncio.gather(*[process_and_save_stop(s, db) for s in chunk])
        await asyncio.sleep(1.5) 

async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"⚠️ 시스템 대기 중: {e}")
        await asyncio.sleep(interval_minutes * 60)
