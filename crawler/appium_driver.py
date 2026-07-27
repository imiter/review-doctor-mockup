"""Appium 세션 시작/종료 + adb 기반 mock GPS 설정."""

import subprocess
import time

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options

# adb emu geo fix로 GPS 값을 바꿔도 앱의 위치 공급자(FusedLocationProvider)가
# 새 값을 실제로 반영하기까지 약간의 지연이 있다 — 실측 결과 이 대기 없이
# 바로 위치 기반 동작(예: "현재 위치로 찾기")을 하면 이전 GPS 값을 그대로
# 쓰는 경우가 있었다.
_GPS_SETTLE_WAIT_SEC = 3


def check_server_ready(url: str = "http://localhost:4723") -> None:
    """실행 시작 전 헬스체크 — Appium 서버가 안 떠 있으면 뭘 확인해야 하는지
    바로 알려준다 (스펙의 '에러 처리' 요구사항)."""
    try:
        res = requests.get(f"{url}/status", timeout=3)
        if not res.ok or not res.json().get("value", {}).get("ready", False):
            raise RuntimeError(f"Appium 서버가 준비되지 않았습니다: {url}/status 응답 이상")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Appium 서버에 연결할 수 없습니다 ({url}). "
            "'appium' 명령으로 서버가 떠 있는지, 에뮬레이터가 'adb devices'에 잡히는지 확인하세요."
        ) from e


def start_session(package: str, device_name: str = "emulator-5554"):
    check_server_ready()
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = device_name
    options.app_package = package
    options.no_reset = True
    return webdriver.Remote("http://localhost:4723", options=options)


def set_mock_location(lat: float, lng: float, device_serial: str = "emulator-5554") -> None:
    """adb emu geo fix는 경도, 위도 순서로 받는다.

    호출 직후 짧게 대기한다 — 위치 공급자가 새 GPS 값을 반영하기 전에
    바로 위치 기반 동작을 하면 이전 값을 읽어오는 경우가 실측으로
    확인됐다."""
    subprocess.run(
        ["adb", "-s", device_serial, "emu", "geo", "fix", str(lng), str(lat)],
        check=True, capture_output=True,
    )
    time.sleep(_GPS_SETTLE_WAIT_SEC)


def restart_app(driver, package: str) -> None:
    driver.terminate_app(package)
    driver.activate_app(package)
