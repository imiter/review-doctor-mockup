"""배민 앱의 accessibility tree가 읽히는지 확인하는 탐색 스크립트.
실행하면서 output/debug/step_N.png 스크린샷을 남기고, Read 도구로 확인해
다음 탭 좌표를 정하는 방식으로 대화형으로 사용한다."""

import sys

from appium import webdriver
from appium.options.android import UiAutomator2Options

PACKAGE = "com.sampleapp"  # Step 2에서 확인 — SPIKE_NOTES.md 참고


def get_driver():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = PACKAGE
    options.no_reset = True
    return webdriver.Remote("http://localhost:4723", options=options)


def screenshot(driver, name):
    path = f"output/debug/{name}.png"
    driver.save_screenshot(path)
    print(f"저장: {path}")


def tap(driver, x, y):
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})


if __name__ == "__main__":
    driver = get_driver()
    action = sys.argv[1] if len(sys.argv) > 1 else "screenshot"
    if action == "screenshot":
        screenshot(driver, "current")
    elif action == "tap":
        tap(driver, int(sys.argv[2]), int(sys.argv[3]))
        screenshot(driver, "after_tap")
    elif action == "dump":
        with open("output/debug/current_page_source.xml", "w") as f:
            f.write(driver.page_source)
        print("저장: output/debug/current_page_source.xml")
    driver.quit()
