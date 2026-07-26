"""5개 지점(가게 + 1/2/3/4km 반경)을 순회하며 배민 카테고리 순위를 실측한다."""

import csv
import datetime
import os
import sys

from appium_driver import restart_app, set_mock_location, start_session
from baemin_navigator import navigate_to_category, scroll_and_collect
from config import RADII_KM, load_settings
from geo_sampling import sample_points
from geocode import GeocodeError, address_to_coords
from rank_finder import find_rank, parse_items

PACKAGE = "com.sampleapp"
OUTPUT_DIR = "output"
CSV_PATH = os.path.join(OUTPUT_DIR, "results.csv")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")


def _classify_rank(result: dict) -> str | int:
    """스펙의 에러 처리 요구사항: 항목을 아예 못 읽었으면(트리 파싱 실패)
    PARSE_ERROR, 항목은 읽었는데 내 가게가 없으면 NOT_FOUND로 구분한다."""
    if result["rank"] is not None:
        return result["rank"]
    if result["total_scanned"] == 0:
        return "PARSE_ERROR"
    return "NOT_FOUND"


def run():
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"설정 오류: {e}")
        sys.exit(1)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    try:
        base_lat, base_lng = address_to_coords(settings.store_address, settings.kakao_api_key)
    except GeocodeError as e:
        print(f"지오코딩 실패, 실행을 중단합니다: {e}")
        sys.exit(1)

    points = [{"radius_km": 0, "bearing_deg": None, "lat": base_lat, "lng": base_lng}]
    points += sample_points(base_lat, base_lng, RADII_KM)

    driver = start_session(PACKAGE)  # 내부적으로 check_server_ready() 호출
    rows = []
    try:
        for point in points:
            set_mock_location(point["lat"], point["lng"])
            restart_app(driver, PACKAGE)
            navigate_to_category(driver, settings.category_label)
            sources = scroll_and_collect(driver, max_scrolls=30)
            items = parse_items(sources)
            result = find_rank(items, settings.store_display_name)
            rank_value = _classify_rank(result)

            timestamp = datetime.datetime.now().isoformat()
            screenshot_name = f"{timestamp.replace(':', '-')}_{point['radius_km']}km.png"
            screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_name)
            driver.save_screenshot(screenshot_path)

            rows.append({
                "timestamp": timestamp,
                "radius_km": point["radius_km"],
                "bearing_deg": point["bearing_deg"],
                "lat": point["lat"],
                "lng": point["lng"],
                "category": settings.category_label,
                "rank": rank_value,
                "total_scanned": result["total_scanned"],
                "ads_above": result["ads_above"],
                "screenshot_path": screenshot_path,
            })
            print(f"[{point['radius_km']}km] rank={rank_value} (scanned {result['total_scanned']}, ads_above {result['ads_above']})")
    finally:
        driver.quit()

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n완료: {CSV_PATH}")


if __name__ == "__main__":
    run()
