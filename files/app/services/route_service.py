import math
import uuid
from app.services import tfl_service

def calculate_turns(waypoints):
    turns = 0
    if len(waypoints) < 3: return 0
    for i in range(1, len(waypoints) - 1):
        p1, p2, p3 = waypoints[i-1], waypoints[i], waypoints[i+1]
        b1 = math.atan2(p2['lon'] - p1['lon'], p2['lat'] - p1['lat'])
        b2 = math.atan2(p3['lon'] - p2['lon'], p3['lat'] - p2['lat'])
        if abs(math.degrees(b2 - b1)) > 45: turns += 1
    return turns

async def recommend_routes(start_lat, start_lon, end_lat, end_lon, pace, dist):
    disruptions = await tfl_service.get_road_disruptions()
    journey = await tfl_service.get_journey_options(start_lat, start_lon, end_lat, end_lon)
    
    scored_routes = []
    for j in journey.get("journeys", [])[:5]:
        waypoints = [] # (좌표 추출 로직 생략 - 이전과 동일)
        # ... 좌표 추출 코드 ...
        turns = calculate_turns(waypoints)
        score = 100 - (turns * 5)
        
        scored_routes.append({
            "route_id": str(uuid.uuid4()), "score": score, "turns": turns,
            "waypoints": waypoints, "status": "쾌적"
        })
    return scored_routes

async def check_route_integrity(user_lat, user_lon, hr, pace):
    disruptions = await tfl_service.get_road_disruptions()
    voice_msg = ""
    if hr > 165: voice_msg += f"심박수가 {hr}으로 높습니다. 속도를 낮추세요. "
    
    danger = any(abs(user_lat - d.get('lat', 0)) < 0.003 for d in disruptions)
    if danger:
        voice_msg += "전방 사고로 신호가 변했습니다. 50미터 앞 우측으로 우회하세요."
    else:
        voice_msg += "경로가 쾌적합니다."
    
    return {"voice_instruction": voice_msg, "should_reroute": danger}
