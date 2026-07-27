"""5개 지점(가게 + 1/2/3/4km 반경)을 순회하며 배민 카테고리 순위를 실측한다."""

import csv
import datetime
import os
import sys

from appium_driver import restart_app, set_mock_location, start_session
from baemin_navigator import navigate_to_category, scroll_and_collect, set_delivery_address_to_current_location
from config import RADII_KM, load_settings
from geo_sampling import sample_points
from geocode import GeocodeError, address_to_coords
from rank_finder import StoreNameUnmatchableError, check_store_name_matchable, find_rank, parse_items

PACKAGE = "com.sampleapp"
OUTPUT_DIR = "output"
CSV_PATH = os.path.join(OUTPUT_DIR, "results.csv")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")
DEBUG_DIR = os.path.join(OUTPUT_DIR, "debug")

CSV_FIELDNAMES = [
    "timestamp", "radius_km", "bearing_deg", "lat", "lng", "category",
    "rank", "total_scanned", "ads_above", "screenshot_path",
]


def _classify_rank(result: dict) -> str | int:
    """스펙의 에러 처리 요구사항: 항목을 아예 못 읽었으면(트리 파싱 실패)
    PARSE_ERROR, 항목은 읽었는데 내 가게가 없으면 NOT_FOUND로 구분한다."""
    if result["rank"] is not None:
        return result["rank"]
    if result["total_scanned"] == 0:
        return "PARSE_ERROR"
    return "NOT_FOUND"


def _dump_parse_error_source(sources: list[str], point_label: str) -> None:
    """PARSE_ERROR(트리에서 아무 항목도 못 읽음) 발생 시 원인 분석용으로
    원본 page_source를 저장한다 (스펙의 에러 처리 요구사항)."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"parse_error_{point_label}.xml")
    with open(path, "w") as f:
        f.write(sources[-1] if sources else "")
    print(f"  PARSE_ERROR 원본 page_source 저장: {path}")


def run():
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"설정 오류: {e}")
        sys.exit(1)

    try:
        check_store_name_matchable(settings.store_display_name)
    except StoreNameUnmatchableError as e:
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
            point_label = f"{point['radius_km']}km"
            timestamp = datetime.datetime.now().isoformat()
            screenshot_path = os.path.join(SCREENSHOT_DIR, f"{timestamp.replace(':', '-')}_{point_label}.png")
            row = {
                "timestamp": timestamp,
                "radius_km": point["radius_km"],
                "bearing_deg": point["bearing_deg"],
                "lat": point["lat"],
                "lng": point["lng"],
                "category": settings.category_label,
                "rank": None,
                "total_scanned": 0,
                "ads_above": 0,
                "screenshot_path": screenshot_path,
            }

            try:
                set_mock_location(point["lat"], point["lng"])
                restart_app(driver, PACKAGE)
                # adb emu geo fix로 GPS만 바꿔서는 배민의 배달 주소가 갱신되지
                # 않는다(실측으로 확인 — 마지막으로 확정한 주소를 캐싱해서
                # 계속 쓴다). 반드시 앱 내 주소 변경 화면을 직접 조작해야
                # 반경별로 실제로 다른 위치의 카테고리 리스트를 보게 된다.
                set_delivery_address_to_current_location(driver)
                navigate_to_category(driver, settings.category_label)
                sources = scroll_and_collect(driver, max_scrolls=30, target_name=settings.store_display_name)
                items = parse_items(sources)
                result = find_rank(items, settings.store_display_name)
                rank_value = _classify_rank(result)

                # scroll_and_collect가 가게를 찾은 직후(또는 최대 스크롤 도달 시점)
                # 반환하므로, 여기서 바로 스크린샷을 찍어야 화면에 가게가 보이는
                # 상태가 남는다 (스펙: "가게를 찾은 시점에 전체 화면 스크린샷").
                driver.save_screenshot(screenshot_path)

                if rank_value == "PARSE_ERROR":
                    _dump_parse_error_source(sources, point_label)

                row["rank"] = rank_value
                row["total_scanned"] = result["total_scanned"]
                row["ads_above"] = result["ads_above"]
                print(f"[{point_label}] rank={rank_value} (scanned {result['total_scanned']}, ads_above {result['ads_above']})")
            except Exception as e:
                # 이 지점에서 무엇이 실패했든(탐색 실패, adb 오류 등) 전체 실행이
                # 중단되지 않는다 — 스펙의 "전체 실행이 중단되지 않는다" 요구사항.
                row["rank"] = "NAV_ERROR"
                try:
                    driver.save_screenshot(screenshot_path)
                except Exception:
                    row["screenshot_path"] = ""
                print(f"[{point_label}] NAV_ERROR: {e}")

            rows.append(row)
    finally:
        driver.quit()

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n완료: {CSV_PATH}")


if __name__ == "__main__":
    run()
