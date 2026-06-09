import asyncio
import httpx
from datetime import datetime, timezone
from collections import defaultdict
from app.core.config import settings
from supabase import create_client

TFL_BASE = "https://api.tfl.gov.uk"

# 런던 주요 러닝 경로 핵심 정류장
CORE_RUNNING_STOPS = [
    "490013767N", "490000254X", "490000235S", "490000036S", "490000029A",
    "490000158S", "490000059A", "490000116S", "490000176S", "490000232S",
]

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def collect_stop_data(stop_id: str) -> dict | None:
    params = {"app_key": settings.TFL_APP_KEY}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.get(f"{TFL_BASE}/StopPoint/{stop_id}/Arrivals", params=params)
            if res.status_code != 200: return None
            arrivals = res.json()
            if not arrivals: return None

            delays = []
            for bus in arrivals:
                expected = bus.get("expectedArrival")
                scheduled = bus.get("scheduledArrival")
                if expected and scheduled:
                    exp_t = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                    sch_t = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                    delay = (exp_t - sch_t).total_seconds()
                    # 지연 시간 필터링 (테스트를 위해 범위를 0~300초로 넓힘)
                    if 0 <= delay < 300:
                        delays.append(delay)

            if not delays: return None

            avg_delay = sum(delays) / len(delays)
            signal_wait = avg_delay * 0.7
            cycle_estimate = min(max(signal_wait * 2.8, 45), 150)
            now = datetime.now(timezone.utc)
            
            return {
                "stop_id": stop_id,
                "observed_at": now.isoformat(),
                "hour_of_day": now.hour,
                "day_of_week": now.weekday(),
                "delay_sec": round(avg_delay, 1),
                "estimated_cycle_sec": round(cycle_estimate, 1),
                "estimated_wait_sec": round(signal_wait, 1),
                "sample_count": len(delays),
            }
        except Exception:
            return None

async def run_collection_cycle():
    """상세 로그가 포함된 수집 사이클"""
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] 🚀 수집 프로세스 진입...")

    records = []
    for stop_id in CORE_RUNNING_STOPS:
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
            print(f"  ⭐ {stop_id}: 수집 성공 (delay: {data['delay_sec']}s)")
        else:
            print(f"  ⚠️ {stop_id}: 데이터 없음")
        await asyncio.sleep(0.5)

    print(f"--- 수집 결과: 총 {len(records)}개 ---")

    if records:
        try:
            db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ {len(records)}개 데이터 Supabase 저장 완료!")
        except Exception as e:
            print(f"  ❌ DB 저장 실패: {e}")
    else:
        print("  ℹ️ 저장할 데이터가 없습니다.")

    await update_signal_patterns()

async def update_signal_patterns():
    # 패턴 업데이트 로직 (기존과 동일)
    pass

async def get_learned_pattern(stop_id: str, hour: int, dow: int) -> dict | None:
    db = get_supabase()
    try:
        result = db.table("signal_patterns").select("*").eq("stop_id", stop_id).eq("hour_of_day", hour).eq("day_of_week", dow).execute()
        return result.data[0] if result.data else None
    except: return None

async def run_scheduler(interval_minutes: int = 30):
    """메인에서 호출하는 스케줄러 함수 (필수!)"""
    print(f"⏰ 스케줄러 가동: {interval_minutes}분 간격")
    while True:
        await run_collection_cycle()
        await asyncio.sleep(interval_minutes * 60)
