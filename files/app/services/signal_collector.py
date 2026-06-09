async def run_collection_cycle():
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] 🚀 수집 프로세스 진입...")

    records = []
    for stop_id in CORE_RUNNING_STOPS:
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
            print(f"  ⭐ {stop_id}: 수집 성공")
        await asyncio.sleep(0.5)

    # --- [강제 테스트 데이터 추가 시작] ---
    # 실시간 데이터가 없어도 DB 연결 확인을 위해 가짜 데이터를 하나 넣습니다.
    test_record = {
        "stop_id": "TEST_STOP_001",
        "observed_at": now.isoformat(),
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
        "delay_sec": 45.0,
        "estimated_cycle_sec": 90.0,
        "estimated_wait_sec": 30.0,
        "sample_count": 1,
    }
    records.append(test_record)
    print(f"  🧪 테스트용 더미 데이터 생성됨 (DB 확인용)")
    # --- [강제 테스트 데이터 추가 끝] ---

    if records:
        try:
            # .execute() 결과가 없으면 에러가 날 수 있으므로 아래와 같이 작성
            response = db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ {len(records)}개 데이터 저장 시도 완료! (Supabase 응답 확인 필요)")
        except Exception as e:
            print(f"  ❌ DB 저장 실패! 에러 내용: {e}")
    else:
        print("  ℹ️ 저장할 데이터가 없습니다.")
