"""배민 앱 내비게이션 — 컨트롤러가 실기기에서 확인한 실제 탐색 절차를 재현한다."""

import time

_HOME_CATEGORY_ENTRY_LABEL = "피자"  # 홈 화면 카테고리 그리드에 항상 노출되는 안전한 진입점
_LOGIN_PROMPT_MARKER_TEXT = "이메일 또는 아이디로 로그인"  # 실수로 로그인 화면이 뜬 경우 감지용


def _find_by_text(driver, label: str):
    return driver.find_elements("xpath", f"//*[@text='{label}']")


def navigate_to_category(driver, category_label: str) -> None:
    """홈 화면에서 카테고리 탭까지 이동해 category_label 탭을 클릭한다."""
    time.sleep(3)  # 홈 화면 카테고리 아이콘 이미지 로딩 대기 (실측: 로딩 전 탭 시 로그인 화면으로 잘못 진입함)

    entry_elements = _find_by_text(driver, _HOME_CATEGORY_ENTRY_LABEL)
    if not entry_elements:
        raise RuntimeError(f"홈 화면에서 '{_HOME_CATEGORY_ENTRY_LABEL}' 카테고리 진입점을 찾지 못했습니다")
    entry_elements[0].click()
    time.sleep(3)

    if _find_by_text(driver, _LOGIN_PROMPT_MARKER_TEXT):
        driver.back()
        time.sleep(2)
        entry_elements = _find_by_text(driver, _HOME_CATEGORY_ENTRY_LABEL)
        if not entry_elements:
            raise RuntimeError("로그인 화면 복구 후 카테고리 진입점을 다시 찾지 못했습니다")
        entry_elements[0].click()
        time.sleep(3)

    if category_label != _HOME_CATEGORY_ENTRY_LABEL:
        target = _find_by_text(driver, category_label)
        if not target:
            raise RuntimeError(f"카테고리 탭을 찾지 못했습니다: {category_label}")
        target[0].click()
        time.sleep(2)


def scroll_and_collect(driver, max_scrolls: int = 30) -> list[str]:
    """리스트를 아래로 스크롤하며 매번 page_source를 누적 수집한다."""
    sources = [driver.page_source]
    size = driver.get_window_size()
    start_y = int(size["height"] * 0.8)
    end_y = int(size["height"] * 0.2)
    x = int(size["width"] * 0.5)

    for _ in range(max_scrolls):
        driver.execute_script("mobile: swipeGesture", {
            "left": x - 10, "top": end_y, "width": 20, "height": start_y - end_y,
            "direction": "up", "percent": 0.75,
        })
        time.sleep(0.5)
        sources.append(driver.page_source)
    return sources
