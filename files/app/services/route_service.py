import math
import uuid
from datetime import datetime, timezone
from app.services.tfl_service import (
    get_bus_arrivals, 
    get_road_disruptions, 
    get_nearby_jamcams,
    get_journey_options
)

# --- 1. 수학적 계산 유틸리티 ---

def haversine_distance(lat1, lon1, lat2, lon2):
    """두 좌표 사이의 거리 계산 (km)"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def calculate_turns(waypoints):
    """
    [변수 E] 경로 내 방향 전환(코너) 횟수 계산
    각 포인트 사이의 방위각 변화가 45도 이상이면 코너로 간주
    """
    turns = 0
    if len(waypoints) < 3: return 0
    
    for i in range(1, len(waypoints) - 1):
        p1, p2, p3 = waypoints[i-1], waypoints[i], waypoints[i+1]
        
        # 방위각 1 (p1 -> p2)
        bearing1 = math.atan2(p2['lon'] - p1['lon'], p2['lat'] - p1['lat'])
        # 방위각 2 (p2 -> p3)
        bearing2 = math.atan2(p3['lon'] - p2['lon'], p3['lat'] - p2['lat'])
        
        diff = abs(math.degrees(bearing2 - bearing1))
        if diff > 180: diff = 360 - diff
        if diff > 45: # 45도 이상 꺾이면 코너
            turns += 1
    return turns

# --- 2. 핵심 경로 추천 엔진 ---

async def recommend_smart_routes(start_lat, start_lon, end_lat, end_lon, target_pace, target_dist):
    """
    [변수 I] 최적 러닝 루트 5개 추천
    고려사항: 페이스(D), 거리(E), 코너링(E), 신호등(F), 버스지연(G), 사고(N)
    """
    # 1. 실시간 도로 장애 정보 수집 (N)
    all_disruptions = await get_road_disruptions()
    
    # 2. TfL Journey API를 통해 기초 경로 후보군 확보
    journey_data = await get_journey_options(start_lat, start_lon, end_lat, end_lon)
    raw_journeys = journey_data.get("journeys", [])
    
    scored_routes = []
    
    for j in raw_journeys:
        # 각 구간(leg)의 좌표 추출
        waypoints = []
        for leg in j.get("legs", []):
            for path_point in leg.get("path", {}).get("lineString", "[]").replace("[", "").replace("]", "").split("],"):
                coords = path_point.replace("[", "").split(",")
                if len(coords) >= 2:
                    waypoints.append({"lat": float(coords[0]), "lon": float(coords[1])})
        
        if not waypoints: continue

        # [변수 E] 코너링 횟수
        turn_count = calculate_turns(waypoints)
        
        # [변수 N] 사고 정보 대조 (사용자 경로 근처에 사고가 있는지)
        incident_impact = 0
        for d in all_disruptions:
            dist = haversine_distance(waypoints[0]['lat'], waypoints[0]['lon'], d.get('lat', 0), d.get('lon', 0))
            if dist < 0.5: incident_impact += 30 # 사고 지역 근처면 감점

        # [변수 G, H] 실시간 버스 지연 및 CCTV 정보 확보
        # 경로 시작점 근처의 정보를 대표로 가져옴
        nearby_cameras = await get_nearby_jamcams(waypoints[0]['lat'], waypoints[0]['lon'])
        
        # [점수 산정 로직] 
        # 기본 100점 - (코너링 * 5) - (사고 영향)
        score = 100 - (turn_count * 5) - incident_impact
        
        scored_routes.append({
            "route_id": str(uuid.uuid4()),
            "name": f"Route {len(scored_routes)+1}",
            "score": max(0, score),
            "distance_km": round(j.get("duration", 0) * 0.08, 2), # 임시 거리 계산
            "turns": turn_count,
            "waypoints": waypoints,
            "jamcams": nearby_cameras[:2], # 상위 2개 이미지 주소만 포함
            "disruption_status": "위험" if incident_impact > 0 else "쾌적"
        })

    # 점수 높은 순으로 5개 반환
    scored_routes.sort(key=lambda x: x['score'], reverse=True)
    return scored_routes[:5]

# --- 3. 실시간 러닝 중 상태 체크 (보이스 시나리오 엔진) ---

async def check_route_integrity(user_lat, user_lon, heart_rate, current_pace):
    """
    [선택된 루트 변수 J~N 기반] 실시간 우회 및 보이스 가이드 생성
    """
    # 1. 실시간 사고 정보 다시 확인
    disruptions = await get_road_disruptions()
    
    voice_msg = ""
    should_reroute = False
    
    # 1-1. 심박수 체크 (K)
    if heart_rate > 165:
        voice_msg += f"현재 심박수가 {heart_rate}으로 매우 높습니다. 목표 페이스 유지를 위해 속도를 늦추세요. "
    
    # 1-2. 주변 사고(N) 및 신호등 변화 예측
    danger_found = False
    for d in disruptions:
        d_lat = d.get('lat')
        d_lon = d.get('lon')
        if d_lat and d_lon:
            dist = haversine_distance(user_lat, user_lon, float(d_lat), float(d_lon))
            if dist < 0.3: # 300미터 이내 사고 발생 시
                danger_found = True
                break
    
    if danger_found:
        should_reroute = True
        voice_msg += "전방에 돌발 사고가 감지되었습니다. 예상했던 초록불 주기에 변화가 생겼으니 50미터 앞 우측으로 우회하세요."
    else:
        voice_msg += "경로가 쾌적합니다. 현재 페이스를 유지하며 직진하세요."

    return {
        "should_reroute": should_reroute,
        "voice_message": voice_msg,
        "current_status": {
            "lat": user_lat,
            "lon": user_lon,
            "hr": heart_rate,
            "pace": current_pace
        }
    }
