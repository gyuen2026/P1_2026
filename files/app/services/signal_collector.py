import asyncio
import httpx
from datetime import datetime, timezone
from app.core.config import settings
from supabase import create_client

TFL_BASE = "https://api.tfl.gov.uk"

# 런던 주요 정류장 ID와 이름/좌표 매핑 (DB에 NULL이 남지 않도록)
STOP_DETAILS = {
    "490013767N": {"name": "Waterloo Station", "lat": 51.5033, "lon": -0.1195},
    "490000254X": {"name": "Southbank", "lat": 51.5071, "lon": -0.1158},
    "490000235S": {"name": "Blackfriars", "lat": 51.5117, "lon": -0.1045},
    "490000036S": {"name": "Borough Market", "lat": 51.5055, "lon": -0.0905},
    "490000029A": {"name": "London Bridge", "lat": 51.5050, "lon": -0.0860},
}

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

async def collect_stop_data(stop_id: str) -> dict | None:
    params = {"app_key": settings.TFL_APP_KEY}
    details = STOP_DETAILS.get(stop_id, {"name": "Unknown", "lat": 0.0, "lon": 0.0})
    
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
                    try:
                        exp_t = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                        sch_t = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                        delay = (exp_t - sch_t).total_seconds()
                        # 조금 더 넓은 범위의 지연 수집 (-30초~300초)
                        if -30 < delay < 300: delays.append(delay)
                    except: continue
            
            if not delays: return None
            
            avg_delay = sum(delays) / len(delays)
            signal_wait = abs(avg_delay) * 0.7
            cycle_estimate = min(max(signal_wait * 2.8, 45), 150)
            now = datetime.now(timezone.utc)
            
            return {
                "stop_id": stop_id,
                "stop_name": details["name"], # 이름 추가
                "lat": details["lat"],         # 위도 추가
                "lon": details["lon"],         # 경도 추가
                "observed_at": now.isoformat(),
                "hour_of_day": now.hour,
                "day_of_week": now.weekday(),
                "delay_sec": round(avg_delay, 1),
                "estimated_cycle_sec": round(cycle_estimate, 1),
                "estimated_wait_sec": round(signal_wait, 1),
                "sample_count": len(delays),
            }
        except: return None

async def run_collection_cycle():
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] 실시간 수집 시작...")
    
    records = []
    for stop_id in STOP_DETAILS.keys():
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
            print(f"  ⭐ {data['stop_name']} 수집 성공")
        await asyncio.sleep(0.5)

    # !!! 중요: 테스트 더미 데이터 생성 코드 삭제됨 !!!

    if records:
        try:
            db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ {len(records)}개 실시간 데이터 저장 완료")
        except Exception as e:
            print(f"  ❌ 저장 실패: {e}")
    else:
        print("  ⚠️ 수집된 실시간 데이터가 없습니다. (현재 런던 버스 정시 운행 중)")

async def run_scheduler(interval_minutes: int = 30):
    while True:
        await run_collection_cycle()
        await asyncio.sleep(interval_minutes * 60)
