"""기준 좌표 + 반경 구간 → 구간 내 랜덤 거리·방위각 좌표. 네트워크/Appium 의존 없음."""

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


def sample_ring_point(
    base_lat: float, base_lng: float, min_km: float, max_km: float, rng: random.Random | None = None
) -> dict:
    """base 좌표를 중심으로 [min_km, max_km] 구간 내 랜덤 거리·방위각 좌표 1개를 뽑는다.

    사용자 확정: 가게 주소를 기점으로 반경 구간(예: 1.5~2.5km)에서 매번
    기점부터 다시 계산한다 — 이전 지점에서 누적으로 이어가지 않는다."""
    rng = rng or random.Random()
    distance_km = rng.uniform(min_km, max_km)
    bearing_deg = rng.uniform(0, 360)
    lat, lng = destination_point(base_lat, base_lng, bearing_deg, distance_km)
    return {"distance_km": round(distance_km, 3), "bearing_deg": round(bearing_deg, 2), "lat": lat, "lng": lng}
