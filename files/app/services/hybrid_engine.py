def get_hybrid_signal_probability(stop_id, arrival_time_from_now):
    """
    ㄱ(실시간 버스 데이터)가 있으면 우선 사용, 
    없으면 ㄴ(90초 주기 지문)을 기반으로 현재 신호 상태 예측
    """
    real_time_data = get_bus_delay_data(stop_id)
    
    if real_time_data: # ㄱ 방식
        cycle = real_time_data['cycle_sec']
        offset = real_time_data['last_red_start']
    else: # ㄴ 방식 (기본 90초 주기 가설 적용)
        cycle = 90 
        # 최초 1회 관측된 빨간불 시작점(Anchor)이 없다면 현재 시간을 기준으로 가설 생성
        offset = get_anchor_from_supabase(stop_id) or DEFAULT_ANCHOR 

    # 현재 시간 기준 신호등 타임라인 내 위치 계산
    current_pos = (datetime.now() + arrival_time_from_now) % cycle
    
    # 초록불 확률 반환 (0.0 ~ 1.0)
    return 0.9 if current_pos < (cycle * 0.45) else 0.1hybrid_engine.py
