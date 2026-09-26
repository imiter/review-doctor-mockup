"""가게 주소(0km) + RING_KM_RANGES에 정의된 반경 지점(기본값: 1km, 2km)에서
각 1지점씩, 총 4개 지점을 순회하며 배민 카테고리 순위를 실측한다. 각 반경
지점은 매번 가게 주소를 기점으로 새 방위각을 뽑아 다시 계산한다 — 이전
지점에서 누적으로 이어가지 않는다."""

import csv
import datetime
import os
import random
import sys

from appium_driver import restart_app, set_mock_location, start_session
from baemin_navigator import (
    MountainLotAddressError,
    dismiss_ad_popup,
    navigate_to_category,
    scroll_and_collect,
    set_delivery_address_to_current_location,
)
from config import RING_KM_RANGES, load_settings
from geo_sampling import sample_ring_point
from geocode import GeocodeError, address_to_coords
from rank_finder import StoreNameUnmatchableError, check_store_name_matchable, find_rank, parse_items

PACKAGE = "com.sampleapp"
OUTPUT_DIR = "output"
CSV_PATH = os.path.join(OUTPUT_DIR, "results.csv")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")
DEBUG_DIR = os.path.join(OUTPUT_DIR, "debug")
MAX_MOUNTAIN_LOT_RETRIES = 5

CSV_FIELDNAMES = [
    "timestamp", "point_label", "distance_km", "bearing_deg", "lat", "lng", "category",
    "rank", "total_scanned", "ads_above", "screenshot_path",
]


class RingSamplingExhaustedError(Exception):
    """반경 구간 내에서 산 지번이 아닌 주소를 최대 재시도 횟수 안에 찾지 못했다."""


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


def _sanitize_label(label: str) -> str:
    """파일명에 못 쓰는 문자를 정리한다 (예: '1.5~2.5km' → '1.5-2.5km')."""
    return label.replace("~", "-").replace(" ", "")


def _format_point_label(min_km: float, max_km: float) -> str:
    """RING_KM_RANGES 항목을 화면/CSV에 쓸 라벨로 바꾼다.

    min_km == max_km(고정 거리 지점, 2026-09-23부터 기본값)면 "1km"처럼
    간단히 쓴다 — "1.0~1.0km"로 나오면 화면에서 고정 거리와 구간을 구분하기
    어렵다. 구간(min < max)이면 기존처럼 "1.5~2.5km" 형식을 그대로 쓴다
    (하위 호환 — 구간 설정으로 되돌려도 그대로 동작)."""
    if min_km == max_km:
        return f"{min_km:g}km"
    return f"{min_km:g}~{max_km:g}km"


def _error_row(point_label: str, distance_km, bearing_deg, lat, lng, category: str, rank_label: str) -> dict:
    return {
        "timestamp": datetime.datetime.now().isoformat(),
        "point_label": point_label,
        "distance_km": distance_km,
        "bearing_deg": bearing_deg,
        "lat": lat,
        "lng": lng,
        "category": category,
        "rank": rank_label,
        "total_scanned": 0,
        "ads_above": 0,
        "screenshot_path": "",
    }


def _crawl_point(driver, settings, point_label: str, distance_km, bearing_deg, lat, lng) -> dict:
    """이미 배달 주소가 설정된 상태에서, 카테고리 진입→스크롤→순위 판별→기록까지 수행한다."""
    timestamp = datetime.datetime.now().isoformat()
    safe_label = _sanitize_label(point_label)
    screenshot_path = os.path.join(SCREENSHOT_DIR, f"{timestamp.replace(':', '-')}_{safe_label}.png")
    row = {
        "timestamp": timestamp,
        "point_label": point_label,
        "distance_km": distance_km,
        "bearing_deg": bearing_deg,
        "lat": lat,
        "lng": lng,
        "category": settings.category_label,
        "rank": None,
        "total_scanned": 0,
        "ads_above": 0,
        "screenshot_path": screenshot_path,
    }

    try:
        # 주소 등록 직후(restart_app 직후)뿐 아니라 카테고리 진입 시점에도
        # 팝업이 뜬 사례가 실측 확인됐다(2026-09-23) — 안전하게 한 번 더 확인.
        dismiss_ad_popup(driver)
        navigate_to_category(driver, settings.category_label)
        # scroll_and_collect의 스크롤 폭이 화면의 32%로 넓어지면서(누락 없이
        # 검증된 최대치) 한 번에 이동하는 거리가 늘었다 — 같은 스캔 깊이를
        # 유지하도록 스크롤 횟수를 그만큼 줄인다(70 * 20/32 ≈ 44).
        sources = scroll_and_collect(driver, max_scrolls=44, target_name=settings.store_display_name)
        items = parse_items(sources)
        result = find_rank(items, settings.store_display_name)
        rank_value = _classify_rank(result)

        # scroll_and_collect가 가게를 찾은 직후(또는 최대 스크롤 도달 시점)
        # 반환하므로, 여기서 바로 스크린샷을 찍어야 화면에 가게가 보이는
        # 상태가 남는다 (스펙: "가게를 찾은 시점에 전체 화면 스크린샷").
        driver.save_screenshot(screenshot_path)

        if rank_value == "PARSE_ERROR":
            _dump_parse_error_source(sources, safe_label)

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

    return row


def _setup_address_for_store(driver, base_lat: float, base_lng: float) -> None:
    """가게 기준 주소(0km 지점) — 재샘플링 없이 1회만 시도한다."""
    set_mock_location(base_lat, base_lng)
    restart_app(driver, PACKAGE)
    dismiss_ad_popup(driver)
    set_delivery_address_to_current_location(driver)


def _setup_address_for_ring(driver, base_lat: float, base_lng: float, min_km: float, max_km: float, rng: random.Random) -> dict:
    """반경 구간 내에서 산 지번이 아닌 주소가 나올 때까지 재샘플링한다.

    매번 가게 주소(base_lat, base_lng)를 기점으로 새로 계산한다 — 직전에
    실패한 산 지번 좌표를 기준으로 이어가지 않는다."""
    for attempt in range(1, MAX_MOUNTAIN_LOT_RETRIES + 1):
        point = sample_ring_point(base_lat, base_lng, min_km, max_km, rng)
        set_mock_location(point["lat"], point["lng"])
        restart_app(driver, PACKAGE)
        dismiss_ad_popup(driver)
        try:
            set_delivery_address_to_current_location(driver)
            return point
        except MountainLotAddressError as e:
            print(f"  [{_format_point_label(min_km, max_km)}] 산 지번 주소라 건너뜀 (시도 {attempt}/{MAX_MOUNTAIN_LOT_RETRIES}): {e}")
    raise RingSamplingExhaustedError(
        f"{MAX_MOUNTAIN_LOT_RETRIES}회 재시도해도 산 지번이 아닌 주소를 찾지 못했습니다 ({_format_point_label(min_km, max_km)})"
    )


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

    if settings.store_lat is not None and settings.store_lng is not None:
        base_lat, base_lng = settings.store_lat, settings.store_lng
    else:
        try:
            base_lat, base_lng = address_to_coords(settings.store_address, settings.kakao_api_key)
        except GeocodeError as e:
            print(f"지오코딩 실패, 실행을 중단합니다: {e}")
            sys.exit(1)

    rng = random.Random()
    driver = start_session(PACKAGE)  # 내부적으로 check_server_ready() 호출
    rows = []
    try:
        # 지점 1: 가게 주소 (0km)
        try:
            _setup_address_for_store(driver, base_lat, base_lng)
            rows.append(_crawl_point(driver, settings, "0km", 0.0, None, base_lat, base_lng))
        except Exception as e:
            print(f"[0km] NAV_ERROR: {e}")
            rows.append(_error_row("0km", 0.0, None, base_lat, base_lng, settings.category_label, "NAV_ERROR"))

        # 지점 2-3: 가게 주소 기준 반경 지점 — 매번 가게 주소부터 다시 계산
        for min_km, max_km in RING_KM_RANGES:
            point_label = _format_point_label(min_km, max_km)
            try:
                point = _setup_address_for_ring(driver, base_lat, base_lng, min_km, max_km, rng)
            except RingSamplingExhaustedError as e:
                print(f"[{point_label}] {e}")
                rows.append(_error_row(point_label, None, None, None, None, settings.category_label, "MOUNTAIN_LOT_ERROR"))
                continue
            except Exception as e:
                print(f"[{point_label}] NAV_ERROR: {e}")
                rows.append(_error_row(point_label, None, None, None, None, settings.category_label, "NAV_ERROR"))
                continue

            rows.append(_crawl_point(
                driver, settings, point_label,
                point["distance_km"], point["bearing_deg"], point["lat"], point["lng"],
            ))
    finally:
        driver.quit()

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n완료: {CSV_PATH}")


if __name__ == "__main__":
    run()
