import asyncio
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_stop_data(stop, db):
    """개별 정류장 데이터 분석 및 저장"""
    stop_id = stop.get("id")
    arrivals = await tfl_service.get_bus_arrivals(stop_id)
    
    if not arrivals or not isinstance(arrivals, list): return
    
    delays = []
    for bus in arrivals:
        exp = bus.get("expectedArrival")
        sch = bus.get("scheduledArrival")
        if exp and sch:
            d = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - 
                 datetime.fromisoformat(sch.replace("Z", "+00:00"))).total_seconds()
            delays.append(d)
    
    if delays:
        avg_delay = sum(delays) / len(delays)
        # 보행자 신호 예측 (지연이 있으면 빨간불 정체로 간주)
        color = "RED" if avg_delay > 20 else "GREEN"
        
        record = {
            "stop_id": stop_id,
            "stop_name": stop.get("commonName"),
            "lat": stop.get("lat"),
            "lon": stop.get("lon"),
            "delay_sec": round(avg_delay, 1),
            "predicted_color": color,
            "observed_at": datetime.now(timezone.utc).isoformat()
        }
        db.table("bus_signal_observations").insert(record).execute()

async def run_global_collection():
    """런던 1~3존 전체 수집 실행 엔진"""
    db = get_supabase()
    print("🌍 런던 1~3존 광역 데이터 수집 시작...")
    
    # 1. 모든 정류장 및 사고 정보 가져오기
    stops = await tfl_service.get_all_stops_in_zones()
    disruptions = await tfl_service.get_road_disruptions()
    print(f"🔎 검색된 정류장: {len(stops)}개, 사고 정보: {len(disruptions)}개")

    # 2. 병렬 수집 (서버 부하를 고려해 50개씩 청크 단위로 진행)
    chunk_size = 50
    for i in range(0, len(stops), chunk_size):
        chunk = stops[i:i + chunk_size]
        tasks = [process_stop_data(stop, db) for stop in chunk]
        await asyncio.gather(*tasks)
        print(f"🚀 {i + len(chunk)}개 정류장 처리 완료...")
        await asyncio.sleep(1) # TfL API 제한 준수

async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"❌ 광역 수집 에러: {e}")
        await asyncio.sleep(interval_minutes * 60)
