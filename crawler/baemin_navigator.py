"""배민 앱 내비게이션 — 컨트롤러가 실기기에서 확인한 실제 탐색 절차를 재현한다."""

import time

_HOME_CATEGORY_ENTRY_LABEL = "피자"  # 홈 화면 카테고리 그리드에 항상 노출되는 안전한 진입점
_LOGIN_PROMPT_MARKER_TEXT = "이메일 또는 아이디로 로그인"  # 실수로 로그인 화면이 뜬 경우 감지용
_CATEGORY_EXPAND_BUTTON_DESC = "메뉴 전체보기 버튼"  # 카테고리 탭 바에 안 보이는 카테고리는 이 버튼으로 펼쳐야 나온다
_FIND_TIMEOUT_SEC = 20  # 앱 재시작 직후에는 홈 화면 로딩이 3초보다 오래 걸리는 경우가 실측으로 확인됨
_POLL_INTERVAL_SEC = 1

# "메뉴 전체보기" 펼침 패널은 accessibility tree에 텍스트 노드가 전혀 노출되지
# 않는 통짜 커스텀 렌더링이다(실측으로 확인 — 스펙에서 우려했던 "리스트가
# 커스텀 렌더링돼 있으면 텍스트가 트리에 노출되지 않는" 시나리오가 바로 이
# 화면에서 발생했다). 그래서 이 패널 안의 카테고리만은 좌표 탭으로 폴백한다.
# 5열 그리드이고, 각 셀의 중심 좌표를 화면 크기 대비 비율로 실측해 고정했다
# (1080x2400 에뮬레이터 기준 실측값을 비율로 환산 — 다른 해상도에서도 상대
# 위치는 유지될 가능성이 높지만 100% 보장되지는 않는다).
_EXPANDED_MENU_GRID = [
    ["홈", "치킨", "중식", "돈까스·회", "피자"],
    ["패스트푸드", "찜·탕", "족발·보쌈", "분식", "카페·디저트"],
    ["한식", "고기", "양식", "아시안", "야식"],
    ["도시락", "민트스타"],
]
_EXPANDED_MENU_COL_X_FRACTIONS = [0.1078, 0.3033, 0.4989, 0.6944, 0.8900]
_EXPANDED_MENU_ROW_Y_FRACTIONS = [0.2215, 0.3095, 0.3955, 0.4810]


def _tap_expanded_menu_category(driver, category_label: str) -> bool:
    """펼침 패널에서 category_label 위치를 좌표로 계산해 탭한다.

    이 패널은 element 기반 탐색이 불가능해(위 설명 참고) 알려진 그리드
    좌표표를 쓴다. category_label이 이 표에 없으면 False를 반환한다."""
    for row_idx, row in enumerate(_EXPANDED_MENU_GRID):
        if category_label not in row:
            continue
        col_idx = row.index(category_label)
        size = driver.get_window_size()
        x = int(size["width"] * _EXPANDED_MENU_COL_X_FRACTIONS[col_idx])
        y = int(size["height"] * _EXPANDED_MENU_ROW_Y_FRACTIONS[row_idx])
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        return True
    return False


def _find_by_text(driver, label: str):
    """label과 정확히 일치하는 노드를 text 또는 content-desc 속성으로 찾는다.

    실측 결과 홈 화면 카테고리 아이콘(Button)은 content-desc로 라벨이
    노출되는 반면, 카테고리 화면 진입 후의 상단 탭 바는 text로 노출된다 —
    이 함수는 두 경우 모두 처리한다."""
    return driver.find_elements(
        "xpath", f"//*[@text='{label}' or @content-desc='{label}']"
    )


def _wait_for_text(driver, label: str, timeout: float = _FIND_TIMEOUT_SEC):
    """label이 화면에 나타날 때까지 짧은 간격으로 폴링한다.

    실측 결과 restart_app 직후에는 (특히 위치 변경 직후) 홈 화면이 완전히
    로딩되기까지 고정 3초보다 오래 걸리는 경우가 있었다 — 단발성 sleep 대신
    폴링으로 대기한다."""
    elapsed = 0.0
    while elapsed < timeout:
        elements = _find_by_text(driver, label)
        if elements:
            return elements
        time.sleep(_POLL_INTERVAL_SEC)
        elapsed += _POLL_INTERVAL_SEC
    return []


def navigate_to_category(driver, category_label: str) -> None:
    """홈 화면에서 카테고리 탭까지 이동해 category_label 탭을 클릭한다."""
    entry_elements = _wait_for_text(driver, _HOME_CATEGORY_ENTRY_LABEL)
    if not entry_elements:
        raise RuntimeError(f"홈 화면에서 '{_HOME_CATEGORY_ENTRY_LABEL}' 카테고리 진입점을 찾지 못했습니다")
    entry_elements[0].click()
    time.sleep(2)

    if _find_by_text(driver, _LOGIN_PROMPT_MARKER_TEXT):
        driver.back()
        time.sleep(2)
        entry_elements = _wait_for_text(driver, _HOME_CATEGORY_ENTRY_LABEL)
        if not entry_elements:
            raise RuntimeError("로그인 화면 복구 후 카테고리 진입점을 다시 찾지 못했습니다")
        entry_elements[0].click()
        time.sleep(2)

    if category_label != _HOME_CATEGORY_ENTRY_LABEL:
        target = _wait_for_text(driver, category_label)
        if not target:
            # 상단 탭 바에 바로 안 보이는 카테고리는 "메뉴 전체보기" 버튼으로
            # 펼친 뒤 다시 찾는다 (실측: "구이"처럼 기본 탭 바에 없는
            # 카테고리가 있었음).
            expand_button = driver.find_elements(
                "xpath", f"//*[@content-desc='{_CATEGORY_EXPAND_BUTTON_DESC}']"
            )
            if not expand_button:
                raise RuntimeError(f"카테고리 탭을 찾지 못했고, 펼치기 버튼도 없습니다: {category_label}")
            expand_button[0].click()
            time.sleep(2)
            if not _tap_expanded_menu_category(driver, category_label):
                raise RuntimeError(f"카테고리 탭을 찾지 못했습니다(펼친 목록에도 없음): {category_label}")
            time.sleep(2)
            return
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
