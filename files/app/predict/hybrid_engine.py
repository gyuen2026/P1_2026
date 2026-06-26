"""
Hybrid signal probability engine.
Delegates to unified signal_prediction module (G + H + N → F).
"""
from app.ingest.signal_collector import get_learned_pattern
from app.predict.signal_prediction import get_london_now, predict_signal_state


async def get_hybrid_signal_probability(
    stop_id: str,
    lat: float,
    lon: float,
    arrival_time_from_now_sec: float = 0,
    disruptions: list | None = None,
    jamcams: list | None = None,
) -> float:
    """
    Return green-light probability (0.0–1.0) for a stop at arrival time.
    Uses learned patterns when available, otherwise real-time bus + JamCam data.
    """
    now = get_london_now()
    hour, dow = now.hour, now.isoweekday()
    learned = await get_learned_pattern(stop_id, hour, dow)

    prediction = predict_signal_state(
        learned=learned,
        jamcams=jamcams or [],
        disruptions=disruptions or [],
        lat=lat,
        lon=lon,
        hour=hour,
    )
    return prediction["green_probability"]
