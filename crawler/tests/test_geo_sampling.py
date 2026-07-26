import math
import random

from geo_sampling import destination_point, sample_points


def test_destination_point_north_1km():
    # 정북(0도)으로 1km 이동하면 위도만 증가하고 경도는 거의 그대로여야 한다
    lat, lng = destination_point(37.6542, 127.0620, bearing_deg=0, distance_km=1)
    assert lat > 37.6542
    assert math.isclose(lng, 127.0620, abs_tol=0.001)


def test_destination_point_distance_is_correct():
    # 임의 방향으로 2km 이동한 실제 거리를 haversine 역계산으로 검증
    lat, lng = destination_point(37.6542, 127.0620, bearing_deg=45, distance_km=2)

    R = 6371.0
    lat1, lng1, lat2, lng2 = map(math.radians, (37.6542, 127.0620, lat, lng))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    computed_km = 2 * R * math.asin(math.sqrt(a))
    assert math.isclose(computed_km, 2, abs_tol=0.01)


def test_sample_points_returns_one_point_per_radius():
    points = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(42))
    assert [p["radius_km"] for p in points] == [1, 2, 3, 4]
    assert all(0 <= p["bearing_deg"] < 360 for p in points)


def test_sample_points_deterministic_with_same_seed():
    a = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(7))
    b = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(7))
    assert a == b
