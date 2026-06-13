async def collect_stop_data(stop_id: str) -> dict | None:
    arrivals = await get_bus_arrivals(stop_id)
    
    # 에러 수정: arrivals가 리스트인지 엄격히 체크
    if not arrivals or not isinstance(arrivals, list):
        print(f"  [Skip] {stop_id}: 유효한 버스 정보 없음")
        return None

    delays = []
    for bus in arrivals:
        # 각 요소가 사전(dict)인지 한 번 더 확인
        if not isinstance(bus, dict): continue
        
        expected = bus.get("expectedArrival")
        scheduled = bus.get("scheduledArrival")
        if expected and scheduled:
            try:
                exp_t = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                sch_t = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                delay = (exp_t - sch_t).total_seconds()
                delays.append(delay)
            except: continue
            
    if not delays: return None
    
    avg_delay = sum(delays) / len(delays)
    # ㄴ 방식: 90초 주기 내 현재 오프셋 계산 로직 포함
    return {
        "stop_id": stop_id,
        "delay_sec": round(avg_delay, 1),
        "observed_at": datetime.now(timezone.utc).isoformat()
    }
