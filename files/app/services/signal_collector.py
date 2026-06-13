import asyncio
import httpx
from datetime import datetime, timezone
from app.services import tfl_service
from supabase import create_client
from app.core.config import settings

def get_supabase():
    # 환경변수 로드 확인용 로그
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("❌ [CRITICAL] Supabase 설정값(URL/KEY)이 없습니다!")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def process_and_save_stop(stop, db):
    stop_id = stop.get("id")
    try:
        # 타임아웃을 걸어 TfL 응답이 늦어지면 과감히 포기
        arrivals = await asyncio.wait_for(tfl_service.get_bus_arrivals(stop_id), timeout=5.0)
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
            # 저장 시에도 타임아웃 5초 설정
            db.table("bus_signal_observations").insert(record).execute()
            print(f"  🟢 [저장성공] {stop.get('commonName')}")
    except asyncio.TimeoutError:
        print(f"  ⏳ [타임아웃] {stop_id} 응답 지연으로 스킵")
    except Exception as e:
        print(f"  🔴 [에러] {stop_id}: {str(e)[:30]}")

async def run_global_collection():
    print(f"\n--- 🚀 [{datetime.now().strftime('%H:%M:%S')}] 수집 엔진 시작 ---")
    db = get_supabase()
    
    # [단계 1] DB 연결 테스트
    print("Step 1: DB 연결 확인 중...")
    try:
        await asyncio.wait_for(
            asyncio.to_thread(db.table("bus_signal_observations").insert({
                "stop_id": "HEARTBEAT", "stop_name": "Check", "delay_sec": 0, "observed_at": datetime.now(timezone.utc).isoformat()
            }).execute), 
            timeout=5.0
        )
        print("✅ Step 1 성공: DB 통로 확보됨")
    except Exception as e:
        print(f"❌ Step 1 실패: DB 연결에 문제가 있습니다 -> {e}")
        return

    # [단계 2] TfL에서 정류장 목록 가져오기
    print("Step 2: TfL 정류장 목록 요청 중 (반경 1km)...")
    try:
        stops_data = await tfl_service.get_all_stops_in_zones(radius=1000) # 1km로 더 축소
        stops = stops_data.get("stopPoints", []) if stops_data else []
        print(f"✅ Step 2 성공: {len(stops)}개 정류장 확보")
    except Exception as e:
        print(f"❌ Step 2 실패: TfL 통신 에러 -> {e}")
        return

    # [단계 3] 실제 수집 및 저장
    print(f"Step 3: {len(stops)}개 정류장 순차 처리 시작...")
    for i in range(0, len(stops), 3): # 3개씩 매우 천천히 처리
        chunk = stops[i:i+3]
        await asyncio.gather(*[process_and_save_stop(s, db) for s in chunk])
        await asyncio.sleep(2.0) # TfL 차단 방지용 긴 휴식
    
    print(f"--- ✨ [{datetime.now().strftime('%H:%M:%S')}] 수집 주기 종료 ---\n")

async def run_scheduler(interval_minutes: int = 30):
    while True:
        try:
            await run_global_collection()
        except Exception as e:
            print(f"⚠️ 스케줄러 중단 방지: {e}")
        await asyncio.sleep(interval_minutes * 60)
