"""기준 좌표 + 반경 목록 → 반경별 랜덤 방위각 좌표. 네트워크/Appium 의존 없음."""

import math
import random

EARTH_RADIUS_KM = 6371.0


def destination_point(lat: float, lng: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """구면 삼각법 destination-point 공식."""
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    bearing = math.radians(bearing_deg)
    angular_dist = distance_km / EARTH_RADIUS_KM

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_dist) + math.cos(lat1) * math.sin(angular_dist) * math.cos(bearing)
    )
    lng2 = lng1 + math.atan2(
        math.sin(bearing) * math.sin(angular_dist) * math.cos(lat1),
        math.cos(angular_dist) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lng2)


def sample_points(base_lat: float, base_lng: float, radii_km: list[int], rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random()
    points = []
    for radius_km in radii_km:
        bearing_deg = rng.uniform(0, 360)
        lat, lng = destination_point(base_lat, base_lng, bearing_deg, radius_km)
        points.append({"radius_km": radius_km, "bearing_deg": round(bearing_deg, 2), "lat": lat, "lng": lng})
    return points
