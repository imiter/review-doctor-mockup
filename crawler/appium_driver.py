"""Appium 세션 시작/종료 + adb 기반 mock GPS 설정."""

import subprocess

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options


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
    """adb emu geo fix는 경도, 위도 순서로 받는다."""
    subprocess.run(
        ["adb", "-s", device_serial, "emu", "geo", "fix", str(lng), str(lat)],
        check=True, capture_output=True,
    )


def restart_app(driver, package: str) -> None:
    driver.terminate_app(package)
    driver.activate_app(package)
