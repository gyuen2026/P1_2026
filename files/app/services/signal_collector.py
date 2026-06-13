import asyncio
import httpx
from datetime import datetime, timezone
from app.core.config import settings
from supabase import create_client

TFL_BASE = "https://api.tfl.gov.uk"

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
            arrivals = res.json()
            
            # 로그: 데이터가 오는지 확인
            print(f"  [Log] {details['name']}: {len(arrivals)}개의 버스 정보 수신")
            
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
                        delays.append(delay) # 필터 없이 모든 데이터 수집
                    except: continue
            
            if not delays: return None
            
            avg_delay = sum(delays) / len(delays)
            now = datetime.now(timezone.utc)
            
            return {
                "stop_id": stop_id,
                "stop_name": details["name"],
                "lat": details["lat"],
                "lon": details["lon"],
                "observed_at": now.isoformat(),
                "hour_of_day": now.hour,
                "day_of_week": now.weekday(),
                "delay_sec": round(avg_delay, 1),
                "estimated_cycle_sec": 90.0, # 기본값 고정
                "estimated_wait_sec": round(abs(avg_delay) * 0.7, 1),
                "sample_count": len(delays),
            }
        except Exception as e:
            print(f"  [Error] {stop_id} 수집 실패: {e}")
            return None

async def run_collection_cycle():
    db = get_supabase()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 수집 주기 시작")
    
    records = []
    for stop_id in STOP_DETAILS.keys():
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
        await asyncio.sleep(1) # 간격 1초로 확대

    if records:
        try:
            # .execute() 결과 출력으로 확실히 저장 여부 판단
            result = db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ DB 저장 완료: {len(records)}개 행 삽입됨")
        except Exception as e:
            print(f"  ❌ DB 저장 최종 실패: {e}")
    else:
        print("  ⚠️ 저장할 유효 데이터가 없습니다.")

async def run_scheduler(interval_minutes: int = 30):
    while True:
        await run_collection_cycle()
        await asyncio.sleep(interval_minutes * 60)
