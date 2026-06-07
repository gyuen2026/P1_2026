import httpx
from app.core.config import settings

OW_BASE = "https://api.openweathermap.org/data/2.5"

async def get_current_weather(lat: float, lon: float) -> dict:
    """
    현재 날씨 조회 → 경로 점수 계산에 반영
    비/강풍 시 특정 경로(지붕 있는 구간 등) 가중치 부여용
    """
    params = {
        "lat": lat,
        "lon": lon,
        "appid": settings.OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "en",
    }
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            res = await client.get(f"{OW_BASE}/weather", params=params)
            res.raise_for_status()
            data = res.json()
            return {
                "temp_c": data["main"]["temp"],
                "feels_like_c": data["main"]["feels_like"],
                "humidity": data["main"]["humidity"],
                "wind_speed_ms": data["wind"]["speed"],
                "condition": data["weather"][0]["main"],       # "Rain", "Clear" 등
                "description": data["weather"][0]["description"],
                "icon": data["weather"][0]["icon"],
                "is_rain": data["weather"][0]["main"] in ["Rain", "Drizzle", "Thunderstorm"],
                "is_windy": data["wind"]["speed"] > 10,        # 10m/s 이상 강풍
            }
        except Exception:
            # API 실패 시 기본값 반환 (서비스 중단 방지)
            return {
                "temp_c": 15.0,
                "feels_like_c": 14.0,
                "humidity": 60,
                "wind_speed_ms": 3.0,
                "condition": "Clear",
                "description": "clear sky",
                "icon": "01d",
                "is_rain": False,
                "is_windy": False,
            }

def get_weather_summary(weather: dict) -> str:
    cond = weather["condition"]
    temp = weather["temp_c"]
    wind = weather["wind_speed_ms"]

    if weather["is_rain"]:
        return f"🌧️ {cond} · {temp:.0f}°C — Wet surface, choose sheltered routes"
    elif weather["is_windy"]:
        return f"💨 Windy · {temp:.0f}°C · {wind:.0f}m/s — Consider wind direction"
    elif temp > 25:
        return f"☀️ Hot · {temp:.0f}°C — Stay hydrated, shaded routes preferred"
    elif temp < 5:
        return f"🥶 Cold · {temp:.0f}°C — Warm up well before starting"
    else:
        return f"✅ Good conditions · {temp:.0f}°C — Perfect for running"
