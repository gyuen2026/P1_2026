import asyncio
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_and_save_stop(stop, db):
    """개별 정류장 데이터 수집 및 저장 (NULL 방지 로직 포함)"""
    stop_id = stop.get("id")
    if not stop_id: return

    try:
        # 실시간 버스 도착 정보 가져오기 (타임아웃 5초)
        arrivals = await asyncio.wait_for(tfl_service.get_bus_arrivals(stop_id), timeout=5.0)
        
        # 데이터가 없거나 리스트가 아니면 스킵
        if not arrivals or not isinstance(arrivals, list): return

        delays = []
        for bus in arrivals:
            if isinstance(bus, dict):
                exp = bus.get("expectedArrival")
                sch = bus.get("scheduledArrival")
                if exp and sch:
                    try:
                        d = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - 
                             datetime.fromisoformat(sch.replace("Z", "+00:00"))).total_seconds()
                        delays.append(d)
                    except: continue
        
        if delays:
            avg_delay = sum(delays) / len(delays)
            now = datetime.now(timezone.utc)
            
            # [데이터 정제] 모든 필드를 NULL 없이 꽉 채웁니다.
            record = {
                "stop_id": stop_id,
                "stop_name": stop.get("commonName", "Unknown Stop"),
                "lat": float(stop.get("lat", 0.0)),
                "lon": float(stop.get("lon", 0.0)),
                "hour_of_day": now.hour,
                "day_of_week": now.weekday(),
                "delay_sec": round(avg_delay, 1),
                "estimated_cycle_sec": 90.0, # 런던 표준 주기 적용
                "predicted_color": "RED" if avg_delay > 20 else "GREEN",
                "observed_at": now.isoformat()
            }
            
            # DB 저장
            db.table("bus_signal_observations").insert(record).execute()
            # 잦은 로그는 서버 부하를 주므로 주요 성공만 표시
            if now.second % 30 == 0: 
                print(f"  📍 수집 중... {stop.get('commonName')[:15]} (지연: {round(avg_delay, 1)}s)")

    except Exception:
        pass # 대량 수집 시 개별 에러는 무시하고 속도 유지

async def run_global_collection():
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"\n--- 🌍 [{now.strftime('%H:%M:%S')}] 런던 1~2존 광역 수집 가동 ---")
    
    # [Step 1] 수집 시작 신호 (Heartbeat) - 모든 필드 채움
    try:
        db.table("bus_signal_observations").insert({
            "stop_id": f"START_{now.strftime('%Y%m%d_%H%M')}",
            "stop_name": "Zone 1-2 Scan Started",
            "lat": 51.5074, "lon": -0.1278,
            "hour_of_day": now.hour, "day_of_week": now.weekday(),
            "delay_sec": 0.0, "observed_at": now.isoformat()
        }).execute()
    except: pass

    # [Step 2] 1~2존 전역 정류장 탐색 (반경 7.5km = 런던 1~2존 커버)
    print("Step 2: 런던 1~2존 정류장 목록 확보 중 (7.5km 반경)...")
    try:
        stops_data = await tfl_service.get_all_stops_in_zones(radius=7500)
        stops = stops_data.get("stopPoints", []) if stops_data else []
        total_count = len(stops)
        print(f"✅ 총 {total_count}개의 정류장을 찾았습니다.")
    except Exception as e:
        print(f"❌ 목록 확보 실패: {e}")
        return

    # [Step 3] 대량 데이터 병렬 처리 (청크 단위 최적화)
    # TfL API 제한을 고려하여 20개씩 묶어서 처리
    print("Step 3: 데이터 수집 및 Supabase 저장 시작...")
    batch_size = 20
    for i in range(0, total_count, batch_size):
        chunk = stops[i:i + batch_size]
        await asyncio.gather(*[process_and_save_stop(s, db) for s in chunk])
        
        # API 차단 방지를 위한 지능형 휴식
        await asyncio.sleep(0.8) 
        
        if i % 200 == 0:
            print(f"   🚀 진행률: {round((i/total_count)*100, 1)}% ({i}/{total_count})")

    print(f"✨ 1~2존 수집 완료! (총 {total_count}개 처리 시도)\n")

async def run_scheduler(interval_minutes: int = 30):
    print(f"🚀 스케줄러가 {interval_minutes}분 간격으로 광역 수집을 수행합니다.")
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"⚠️ 주기 에러 발생: {e}")
        
        print(f"💤 다음 수집까지 {interval_minutes}분 휴식...")
        await asyncio.sleep(interval_minutes * 60)
