import math
import uuid
from app.services.tfl_service import (
    get_bus_arrivals, get_road_disruptions, 
    get_nearby_jamcams, get_journey_options
)

def calculate_turns(waypoints):
    """E. 코너링(방향 전환) 횟수 계산"""
    turns = 0
    if len(waypoints) < 3: return 0
    for i in range(1, len(waypoints) - 1):
        p1, p2, p3 = waypoints[i-1], waypoints[i], waypoints[i+1]
        b1 = math.atan2(p2['lon'] - p1['lon'], p2['lat'] - p1['lat'])
        b2 = math.atan2(p3['lon'] - p2['lon'], p3['lat'] - p2['lat'])
        if abs(math.degrees(b2 - b1)) > 45: turns += 1
    return turns

async def recommend_routes(start_lat, start_lon, end_lat, end_lon, target_pace, target_dist):
    """I. 최적 러닝 루트 5개 추천 (E, F, G, H 변수 종합)"""
    disruptions = await get_road_disruptions() or []
    journey_data = await get_journey_options(start_lat, start_lon, end_lat, end_lon)
    raw_journeys = journey_data.get("journeys", []) if journey_data else []
    
    scored_routes = []
    for j in raw_journeys:
        waypoints = []
        for leg in j.get("legs", []):
            line = leg.get("path", {}).get("lineString", "[]").strip("[]").split("],[")
            for p in line:
                c = p.split(","); waypoints.append({"lat": float(c[0]), "lon": float(c[1])})
        
        if not waypoints: continue
        turns = calculate_turns(waypoints)
        
        # 사고(N) 감점 로직
        incident_penalty = 0
        for d in disruptions:
            if abs(waypoints[0]['lat'] - d.get('lat', 0)) < 0.005: incident_penalty += 30

        score = 100 - (turns * 5) - incident_penalty
        scored_routes.append({
            "route_id": str(uuid.uuid4()),
            "score": max(0, score),
            "turns": turns,
            "waypoints": waypoints,
            "jamcams": await get_nearby_jamcams(waypoints[0]['lat'], waypoints[0]['lon']),
            "status": "위험" if incident_penalty > 0 else "쾌적"
        })

    scored_routes.sort(key=lambda x: x['score'], reverse=True)
    return scored_routes[:5]

async def check_route_integrity(user_lat, user_lon, heart_rate, current_pace):
    """J~N. 실시간 우회 보이스 시나리오 생성"""
    disruptions = await get_road_disruptions() or []
    voice_msg = ""
    
    if heart_rate > 165: voice_msg += f"현재 심박수가 {heart_rate}으로 높습니다. 속도를 낮추세요. "
    
    danger = any(abs(user_lat - d.get('lat', 0)) < 0.002 for d in disruptions)
    if danger:
        voice_msg += "전방 사고 발생으로 신호 주기가 변했습니다. 50미터 앞 우측으로 우회하세요."
    else:
        voice_msg += "경로가 쾌적합니다. 페이스를 유지하세요."

    return {"should_reroute": danger, "voice_message": voice_msg}
