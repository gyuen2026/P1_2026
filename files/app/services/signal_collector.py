import asyncio
import httpx
from datetime import datetime, timezone
from app.core.config import settings
from supabase import create_client
from app.services.tfl_service import get_bus_arrivals, get_nearby_jamcams

def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# 런던 주요 정류장 (G 변수 추적용)
STOP_POINTS = {
    "490013767N": "Waterloo Station",
    "490000029A": "London Bridge",
    "490000235S": "Blackfriars"
}

async def analyze_signal_state(stop_id, lat, lon):
    """
    [G+H -> F] 실시간 버스 지연(G)과 도로 영상(H) 정보를 조합해 신호등 색깔(F) 예측
    """
    arrivals = await get_bus_arrivals(stop_id)
    jamcams = await get_nearby_jamcams(lat, lon, radius=200)
    
    delay_sec = 0
    if arrivals and isinstance(arrivals, list):
        delays = []
        for bus in arrivals:
            if isinstance(bus, dict):
                exp = bus.get("expectedArrival")
                sch = bus.get("scheduledArrival")
                if exp and sch:
                    d = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - 
                         datetime.fromisoformat(sch.replace("Z", "+00:00"))).total_seconds()
                    delays.append(d)
        if delays: delay_sec = sum(delays) / len(delays)

    # 신호등 상태 예측 로직 (단순화)
    # 지연이 30초 이상이고 주변에 CCTV가 많다면 정체(빨간불) 확률 높음
    predicted_color = "GREEN" if delay_sec < 20 else "RED"
    
    return {
        "stop_id": stop_id,
        "delay_sec": round(delay_sec, 1),
        "predicted_color": predicted_color,
        "jamcam_url": jamcams[0]["imageUrl"] if jamcams else None,
        "observed_at": datetime.now(timezone.utc).isoformat()
    }

async def run_collection_cycle():
    """데이터 수집 주기 실행"""
    db = get_supabase()
    records = []
    # 예시 좌표 (Waterloo 기준)
    data = await analyze_signal_state("490013767N", 51.5033, -0.1195)
    records.append(data)
    
    if records:
        try:
            db.table("bus_signal_observations").insert(records).execute()
            print(f"✅ 신호등 예측 데이터 저장 완료: {data['predicted_color']}")
        except Exception as e:
            print(f"❌ DB 저장 실패: {e}")

async def run_scheduler(interval_minutes: int = 30):
    """[중요] main.py가 호출하는 스케줄러 함수"""
    print(f"🚀 신호 데이터 수집 스케줄러 시작 ({interval_minutes}분 간격)")
    while True:
        await run_collection_cycle()
        await asyncio.sleep(interval_minutes * 60)
