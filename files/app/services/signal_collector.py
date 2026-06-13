import asyncio
from datetime import datetime, timezone, timedelta
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def get_london_time():
    """런던 현지 시간(BST, UTC+1) 계산"""
    return datetime.now(timezone.utc) + timedelta(hours=1)

async def process_and_save_stop(stop, db):
    """실제 개별 정류장 데이터를 상세히 저장"""
    stop_id = stop.get("id")
    stop_name = stop.get("commonName", "Unknown Station")
    lat = float(stop.get("lat", 0.0))
    lon = float(stop.get("lon", 0.0))

    try:
        arrivals = await asyncio.wait_for(tfl_service.get_bus_arrivals(stop_id), timeout=5.0)
        if not arrivals or not isinstance(arrivals, list): return

        delays = []
        for bus in arrivals:
            if isinstance(bus, dict):
                exp, sch = bus.get("expectedArrival"), bus.get("scheduledArrival")
                if exp and sch:
                    try:
                        d = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - 
                             datetime.fromisoformat(sch.replace("Z", "+00:00"))).total_seconds()
                        delays.append(d)
                    except: continue
        
        if delays:
            avg_delay = sum(delays) / len(delays)
            now_london = get_london_time()
            
            # [최종 데이터] 모든 필드를 NULL 없이 꽉 채움
            record = {
                "stop_id": stop_id,
                "stop_name": stop_name, # 뭉뚱그리지 않고 실제 이름 입력
                "lat": lat,
                "lon": lon,
                "hour_of_day": now_london.hour, # 런던 시간 기준 (17시)
                "day_of_week": now_london.isoweekday(), # 월=1...토=6, 일=7
                "delay_sec": round(avg_delay, 1),
                "estimated_cycle_sec": 90.0, # 기본값 채움
                "estimated_wait_sec": round(abs(avg_delay) * 0.7, 1),
                "predicted_color": "RED" if avg_delay > 20 else "GREEN",
                "observed_at": now_london.isoformat()
            }
            db.table("bus_signal_observations").insert(record).execute()
    except:
        pass

async def run_global_collection():
    db = get_supabase()
    now_london = get_london_time()
    print(f"🌍 [{now_london.strftime('%H:%M:%S')}] 런던 1-2존 수집 시작 (요일:{now_london.isoweekday()}, 시간:{now_london.hour})")
    
    # 정류장 목록 확보
    stops_data = await tfl_service.get_all_stops_in_zones(radius=7500)
    stops = stops_data.get("stopPoints", []) if stops_data else []
    
    if not stops:
        print("⚠️ 정류장 목록을 가져오지 못했습니다.")
        return

    print(f"🔎 {len(stops)}개 정류장 분석 및 개별 저장 시작...")

    # 10개씩 병렬 처리하여 속도와 안정성 모두 확보
    batch_size = 10
    for i in range(0, len(stops), batch_size):
        chunk = stops[i:i + batch_size]
        await asyncio.gather(*[process_and_save_stop(s, db) for s in chunk])
        await asyncio.sleep(0.5) # TfL API 보호

    print(f"✨ 수집 주기가 성공적으로 끝났습니다.")

async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"⚠️ 시스템 오류: {e}")
        await asyncio.sleep(interval_minutes * 60)
