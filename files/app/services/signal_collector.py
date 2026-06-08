"""
신호 패턴 수집기
- 런던 주요 러닝 경로 버스 정류장 데이터를 30분마다 수집
- Supabase에 저장 → 패턴 학습
- 내일 러닝 시 예측에 활용
"""
import asyncio
import httpx
from datetime import datetime, timezone
from app.core.config import settings
from supabase import create_client

TFL_BASE = "https://api.tfl.gov.uk"

# 런던 주요 러닝 경로 핵심 정류장 (Thames Path, Southbank 등)
CORE_RUNNING_STOPS = [
    "490013767N",  # Waterloo Station
    "490000254X",  # Southbank
    "490000235S",  # Blackfriars
    "490000036S",  # Borough Market
    "490000029A",  # London Bridge
    "490000158S",  # Monument
    "490000059A",  # Embankment
    "490000116S",  # Lambeth Bridge
    "490000176S",  # Vauxhall
    "490000232S",  # Westminster Bridge
]


def get_supabase():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


async def collect_stop_data(stop_id: str) -> dict | None:
    """
    단일 정류장 버스 도착 데이터 수집 → 신호 대기 추정
    """
    params = {"app_key": settings.TFL_APP_KEY}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.get(
                f"{TFL_BASE}/StopPoint/{stop_id}/Arrivals",
                params=params
            )
            if res.status_code != 200:
                return None

            arrivals = res.json()
            if not arrivals:
                return None

            # 지연 시간 추출
            delays = []
            for bus in arrivals:
                expected = bus.get("expectedArrival", "")
                scheduled = bus.get("scheduledArrival", "")
                if expected and scheduled:
                    try:
                        exp_t = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                        sch_t = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                        delay = (exp_t - sch_t).total_seconds()
                        if 5 < delay < 180:
                            delays.append(delay)
                    except Exception:
                        continue

            if not delays:
                return None

            avg_delay = sum(delays) / len(delays)
            signal_wait = avg_delay * 0.7
            cycle_estimate = min(max(signal_wait * 2.8, 45), 150)
            green_prob = max(0.1, min(0.9, 1 - (signal_wait / cycle_estimate)))

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

        except Exception as e:
            print(f"Error collecting {stop_id}: {e}")
            return None


async def run_collection_cycle():
    """
    모든 핵심 정류장 데이터 수집 → Supabase 저장
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] 수집 시작... ({len(CORE_RUNNING_STOPS)}개 정류장)")

    collected = 0
    records = []

    for stop_id in CORE_RUNNING_STOPS:
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
            collected += 1
        await asyncio.sleep(0.5)  # API 요청 간격

    if records:
        try:
            db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ {collected}개 정류장 데이터 저장 완료")
        except Exception as e:
            print(f"  ❌ DB 저장 실패: {e}")

    # 패턴 업데이트
    await update_signal_patterns()
    return collected


async def update_signal_patterns():
    """
    누적 데이터로 시간대별 신호 패턴 업데이트
    """
    db = get_supabase()
    try:
        # 최근 7일 데이터 기반 패턴 계산
        obs = db.table("bus_signal_observations")\
            .select("stop_id,hour_of_day,day_of_week,estimated_wait_sec,estimated_cycle_sec")\
            .execute()

        if not obs.data:
            return

        # 정류장 × 시간대 × 요일 별 평균 계산
        from collections import defaultdict
        groups = defaultdict(list)
        for row in obs.data:
            key = (row["stop_id"], row["hour_of_day"], row["day_of_week"])
            groups[key].append({
                "wait": row["estimated_wait_sec"],
                "cycle": row["estimated_cycle_sec"],
            })

        patterns = []
        for (stop_id, hour, dow), samples in groups.items():
            avg_wait = sum(s["wait"] for s in samples) / len(samples)
            avg_cycle = sum(s["cycle"] for s in samples) / len(samples)
            green_prob = max(0.1, min(0.9, 1 - (avg_wait / avg_cycle)))

            patterns.append({
                "stop_id": stop_id,
                "hour_of_day": hour,
                "day_of_week": dow,
                "avg_wait_sec": round(avg_wait, 1),
                "avg_cycle_sec": round(avg_cycle, 1),
                "green_probability": round(green_prob, 2),
                "observation_count": len(samples),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })

        if patterns:
            # upsert로 기존 패턴 업데이트
            db.table("signal_patterns").upsert(
                patterns,
                on_conflict="stop_id,hour_of_day,day_of_week"
            ).execute()
            print(f"  📊 {len(patterns)}개 패턴 업데이트 완료")

    except Exception as e:
        print(f"  ❌ 패턴 업데이트 실패: {e}")


async def get_learned_pattern(stop_id: str, hour: int, dow: int) -> dict | None:
    """
    학습된 패턴 조회 → bus_signal_service에서 이 값 우선 사용
    """
    db = get_supabase()
    try:
        result = db.table("signal_patterns")\
            .select("*")\
            .eq("stop_id", stop_id)\
            .eq("hour_of_day", hour)\
            .eq("day_of_week", dow)\
            .execute()

        if result.data:
            return result.data[0]
    except Exception:
        pass
    return None


async def run_scheduler(interval_minutes: int = 30):
    """
    스케줄러: interval_minutes마다 데이터 수집
    터미널에서 직접 실행: python3.11 -m app.services.signal_collector
    """
    print(f"🚀 신호 패턴 수집기 시작 (매 {interval_minutes}분마다)")
    print(f"   오늘 하루 수집 → 내일 러닝 예측에 사용")
    print(f"   종료: Ctrl+C\n")

    while True:
        await run_collection_cycle()
        print(f"   다음 수집: {interval_minutes}분 후\n")
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(run_scheduler(interval_minutes=30))
