async def run_collection_cycle():
    db = get_supabase()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] 수집 프로세스 진입...")

    collected = 0
    records = []

    for stop_id in CORE_RUNNING_STOPS:
        print(f"  - {stop_id} 데이터 요청 중...") # 로그 추가
        data = await collect_stop_data(stop_id)
        if data:
            records.append(data)
            collected += 1
            print(f"    ⭐ {stop_id} 수집 성공")
        else:
            print(f"    ⚠️ {stop_id} 데이터 없음 (정시 도착 또는 API 오류)")
        await asyncio.sleep(0.5)

    print(f"--- 수집 결과: 총 {len(records)}개 ---") # 로그 추가

    if records:
        try:
            print("  DB 저장 시도 중...")
            db.table("bus_signal_observations").insert(records).execute()
            print(f"  ✅ {collected}개 데이터 저장 완료!")
        except Exception as e:
            print(f"  ❌ DB 저장 실패: {e}")
    else:
        print("  ℹ️ 저장할 데이터가 없습니다. (런던 버스 지연 정보 없음)")

    await update_signal_patterns()
    return collected
