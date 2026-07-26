"""Appium 서버가 에뮬레이터를 제어할 수 있는지 확인하는 스모크 테스트.
배민 앱 없이 안드로이드 기본 설정 앱으로 검증한다."""

from appium import webdriver
from appium.options.android import UiAutomator2Options


def main():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = "com.android.settings"
    options.app_activity = ".Settings"
    options.no_reset = True

    driver = webdriver.Remote("http://localhost:4723", options=options)
    try:
        assert len(driver.page_source) > 100, "page_source가 비어 있음"
        print("OK: Appium이 에뮬레이터를 정상적으로 조작합니다.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
