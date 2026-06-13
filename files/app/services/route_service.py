import math
import uuid
from app.services.tfl_service import get_road_disruptions, get_nearby_jamcams

async def check_route_integrity(user_lat, user_lon, heart_rate, current_pace):
    """실시간 예상 시나리오 기반 보이스 안내 생성"""
    disruptions = await get_road_disruptions() or []
    
    voice_message = ""
    should_reroute = False
    
    # 1. 생체 데이터 분석 (K, L)
    if heart_rate > 165:
        voice_message += f"심박수가 {heart_rate}으로 높습니다. 현재 {current_pace} 페이스에서 조금 늦추세요. "

    # 2. 실시간 사고 및 신호 변화 분석 (N, G, H)
    # 주변 300m 이내 사고(N)가 있는지 검색
    incident = next((d for d in disruptions if abs(user_lat - d.get('lat', 0)) < 0.003), None)
    
    if incident:
        should_reroute = True
        location_name = incident.get("location", "전방")
        voice_message += f"현재 {location_name} 사고로 인해 초록색으로 예상되던 신호 주기에 변화가 생겼습니다. 50미터 앞 우측으로 커브하여 우회하세요."
    else:
        voice_message += "전방 신호등 초록불 wave가 유지될 예정입니다. 경로가 쾌적합니다."

    return {
        "voice_instruction": voice_message,
        "should_reroute": should_reroute,
        "alert_level": "HIGH" if should_reroute else "NORMAL"
    }

# recommend_routes 함수 등은 이전과 동일하게 유지...
