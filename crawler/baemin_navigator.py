"""배민 앱 내비게이션 — 컨트롤러가 실기기에서 확인한 실제 탐색 절차를 재현한다."""

import re
import time

from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.interaction import POINTER_TOUCH
from selenium.webdriver.common.actions.pointer_input import PointerInput

# "산56-21", "산150"처럼 임야(산 지번) 주소를 감지한다. "부산", "온산읍"처럼
# 산 뒤에 숫자가 바로 오지 않는 지명은 걸리지 않는다 — 사용자 요청: 산 지번은
# 실제 배달 가능 위치로 보기 어려워 반경 샘플링에서 제외한다.
_MOUNTAIN_LOT_PATTERN = re.compile(r"(?<!\S)산\d")


class MountainLotAddressError(Exception):
    """역지오코딩된 주소가 산 지번(임야)이라 배달 주소로 부적절할 때 발생한다."""


def contains_mountain_lot(address_text: str) -> bool:
    """주소 문자열에 산 지번(임야) 패턴이 있는지 확인한다. 순수 함수 — 단위 테스트 가능."""
    return bool(_MOUNTAIN_LOT_PATTERN.search(address_text))


_HOME_CATEGORY_ENTRY_LABEL = "피자"  # 홈 화면 카테고리 그리드에 항상 노출되는 안전한 진입점
_LOGIN_PROMPT_MARKER_TEXT = "이메일 또는 아이디로 로그인"  # 실수로 로그인 화면이 뜬 경우 감지용
_CATEGORY_EXPAND_BUTTON_DESC = "메뉴 전체보기 버튼"  # 카테고리 탭 바에 안 보이는 카테고리는 이 버튼으로 펼쳐야 나온다
_FIND_TIMEOUT_SEC = 20  # 앱 재시작 직후에는 홈 화면 로딩이 3초보다 오래 걸리는 경우가 실측으로 확인됨
_POLL_INTERVAL_SEC = 1

# 배달 주소 변경 흐름 (실측으로 확인 — adb emu geo fix로 GPS만 바꾸는 것으로는
# 부족하다). 배민은 배달 주소를 "마지막으로 확정한 주소"로 캐싱해두고, 앱
# 재시작이나 GPS 변경만으로는 절대 자동으로 갱신하지 않는다. 반드시 아래
# 순서로 화면을 직접 조작해야 한다:
#   1. 홈 화면 상단 주소 선택 버튼 탭 → "주소 설정" 화면
#   2. "현재 위치로 찾기" 탭 → 지도 화면으로 이동, GPS 기준 역지오코딩 진행
#   3. 역지오코딩 완료 대기(=주소 등록 버튼이 활성화될 때까지)
#   4. "이 위치로 주소 등록" 탭 → "주소 상세" 화면
#   5. 상세주소 입력칸(첫 번째 EditText)에 아무 값이나 입력해야
#      "주소 등록" 버튼이 활성화된다(빈 값이면 비활성 상태로 남는다)
#   6. "주소 등록" 탭 → 홈 화면으로 복귀, 주소가 갱신돼 있다
#      단, 저장된 주소가 이미 10개면("주소는 10개까지 등록할 수 있어요.
#      가장 오래된 주소를 지우고 새 주소를 등록할까요?") 확인 팝업이
#      추가로 뜬다 — "저장"을 눌러야 최종 등록이 완료된다(실측으로 확인:
#      이 팝업을 처리하지 않으면 화면이 "주소 상세"에 멈춰 있어 다음
#      단계에서 홈 화면 요소를 못 찾고 실패한다).
_ADDRESS_SELECTOR_CONTENT_DESC_MARKER = "현재 위치를 변경할 수 있습니다"
_FIND_CURRENT_LOCATION_LABEL = "현재 위치로 찾기"
_REGISTER_MAP_LOCATION_LABEL = "이 위치로 주소 등록"
_REGISTER_ADDRESS_LABEL = "주소 등록"
_ADDRESS_LIMIT_DIALOG_SAVE_LABEL = "저장"  # "주소 10개 한도" 확인 팝업의 저장 버튼
_ADDRESS_DETAIL_PLACEHOLDER = "1"  # 상세주소는 내용 자체가 중요하지 않다 — 등록 버튼 활성화 조건일 뿐
_REVERSE_GEOCODE_WAIT_SEC = 7  # 지도 로딩 + 역지오코딩에 실측으로 5~7초 걸림
_ADDRESS_LIMIT_DIALOG_WAIT_SEC = 2  # 팝업이 뜬다면 이 시간 안에 나타난다(실측)
# slow_scroll_test.py로 사람이 눈으로 직접 누락 여부를 확인하며 튜닝한 값
# (사용자 실측 검증 완료). ActionBuilder 기반 부드러운 터치 드래그
# (_smooth_drag)로 바꾸면서 스와이프 자체에 걸리는 시간이 이전
# mobile: swipeGesture 방식보다 커져, 스크롤 직후 별도 대기가 사실상
# 필요 없어졌다.
_SCROLL_SETTLE_WAIT_SEC = 0.0001  # 스크롤 직후 리스트 렌더링 완료 대기
_DRAG_SUB_STEPS = 1  # 한 번의 스크롤을 몇 단계로 나눠 부드럽게 이동할지(정수만 가능, range()에 사용됨)
_DRAG_SUB_STEP_PAUSE_SEC = 0.0004
_INITIAL_LOAD_SETTLE_CAPTURES = 3  # 카테고리 진입 직후 스크롤 시작 전 재캡처 횟수(최상단 카드 렌더링 대기)
_INITIAL_LOAD_SETTLE_INTERVAL_SEC = 1.0


def _smooth_drag(driver, x: int, start_y: int, end_y: int) -> None:
    """마우스로 천천히 드래그하듯, 중간 지점을 거쳐 부드럽게 스크롤한다.

    slow_scroll_test.py에서 검증된 것과 동일한 방식 — mobile: swipeGesture
    보다 accessibility tree 캡처 누락이 적다는 게 실측으로 확인됐다."""
    actions = ActionBuilder(driver, mouse=PointerInput(POINTER_TOUCH, "touch"))
    actions.pointer_action.move_to_location(x, start_y)
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.1)
    for i in range(1, _DRAG_SUB_STEPS + 1):
        y = start_y + (end_y - start_y) * i / _DRAG_SUB_STEPS
        actions.pointer_action.move_to_location(x, int(y))
        actions.pointer_action.pause(_DRAG_SUB_STEP_PAUSE_SEC)
    actions.pointer_action.release()
    actions.perform()


def set_delivery_address_to_current_location(driver) -> None:
    """set_mock_location으로 바꾼 GPS를 실제 배달 주소에 반영시킨다.

    adb emu geo fix만으로는 배민 앱의 배달 주소가 바뀌지 않는다(실측으로
    확인 — 캐싱된 마지막 확정 주소를 계속 쓴다). 이 함수가 그 간극을
    메운다. 홈 화면 상태에서 호출해야 한다."""
    address_button = _wait_for_xpath(
        driver, f"//*[contains(@content-desc, '{_ADDRESS_SELECTOR_CONTENT_DESC_MARKER}')]"
    )
    if not address_button:
        raise RuntimeError("홈 화면에서 주소 선택 버튼을 찾지 못했습니다")
    address_button[0].click()
    time.sleep(1.5)

    find_current = _wait_for_text(driver, _FIND_CURRENT_LOCATION_LABEL, timeout=10)
    if not find_current:
        raise RuntimeError("'주소 설정' 화면에서 '현재 위치로 찾기' 버튼을 찾지 못했습니다")
    find_current[0].click()

    time.sleep(_REVERSE_GEOCODE_WAIT_SEC)
    register_map = _wait_for_text(driver, _REGISTER_MAP_LOCATION_LABEL, timeout=10)
    if not register_map:
        raise RuntimeError("지도 화면에서 '이 위치로 주소 등록' 버튼을 찾지 못했습니다")

    # 역지오코딩된 도로명/지번 주소가 화면에 text="..."로 노출된다 — 산
    # 지번이면 등록하지 않고 호출부(run_crawl.py)가 다른 좌표로 재샘플링할
    # 수 있도록 예외를 던진다.
    address_texts = re.findall(r'text="([^"]*)"', driver.page_source)
    mountain_matches = [t for t in address_texts if contains_mountain_lot(t)]
    if mountain_matches:
        raise MountainLotAddressError(f"역지오코딩된 주소가 산 지번(임야)입니다: {mountain_matches}")

    register_map[0].click()
    time.sleep(2)

    detail_inputs = driver.find_elements("class name", "android.widget.EditText")
    if not detail_inputs:
        raise RuntimeError("'주소 상세' 화면에서 상세주소 입력칸을 찾지 못했습니다")
    detail_inputs[0].clear()  # 이전 시도에서 남은 값이 있을 수 있어 먼저 비운다
    detail_inputs[0].send_keys(_ADDRESS_DETAIL_PLACEHOLDER)
    time.sleep(1)

    register_address = _wait_for_text(driver, _REGISTER_ADDRESS_LABEL, timeout=10)
    if not register_address:
        raise RuntimeError("'주소 상세' 화면에서 '주소 등록' 버튼을 찾지 못했습니다")
    register_address[0].click()
    time.sleep(2)

    limit_dialog_save = _wait_for_text(driver, _ADDRESS_LIMIT_DIALOG_SAVE_LABEL, timeout=_ADDRESS_LIMIT_DIALOG_WAIT_SEC)
    if limit_dialog_save:
        limit_dialog_save[0].click()
        time.sleep(2)

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


# fetch_shop_info API 카테고리가 실제 탭과 접두사 관계조차 아닌, 배민
# 자체적으로 API 분류와 화면 탭 분류가 서로 다른 경우를 위한 명시적
# 치환표다 — 접두사 매칭(_match_grid_column)으로는 구조적으로 잡을 수
# 없다("분식"은 "백반·죽·국수"의 접두사가 아니다). 실측/사용자 확인으로
# 알려진 것만 적어둔다(2026-08-31, 행복가성비 컵밥&우동 캠페인 — 사용자가
# 실제 배민 앱에서 이 매장이 "분식" 탭에 있다고 확인). 모르는 값은 그대로
# 통과시켜 기존 정확/접두사 매칭 경로가 이어서 시도하게 한다.
_CATEGORY_LABEL_OVERRIDES = {
    "백반·죽·국수": "분식",
}


def _resolve_category_label(category_label: str) -> str:
    return _CATEGORY_LABEL_OVERRIDES.get(category_label, category_label)


def _match_grid_column(row: list[str], category_label: str) -> int | None:
    """그리드 라벨 한 행에서 category_label과 매칭되는 열 인덱스를 찾는다.

    정확히 일치하면 바로 반환하고, 아니면 그리드 라벨이 category_label의
    접두사인 경우도 매칭으로 인정한다 — 배민 가게정보 API(fetch_shop_info가
    돌려주는 카테고리)가 화면의 "메뉴 전체보기" 탭보다 더 세분화된 값을
    주는 경우가 실 계정에서 확인됐다(2026-08-31, 곱도리탕 캠페인 순위
    확인 시도 중 재현): API는 "찜·탕·찌개"인데 실제 탭은 "찜·탕"이라 정확
    일치 검사가 항상 실패해 모든 지점이 NAV_ERROR로 스킵됐다. "·"로
    하위분류가 이어붙는 배민 카테고리 표기 특성상, 탭 라벨이
    category_label의 접두사이면 같은 카테고리로 취급해도 안전하다
    (예: "고기" ⊂ "고기·구이")."""
    if category_label in row:
        return row.index(category_label)
    for idx, label in enumerate(row):
        if category_label.startswith(label):
            return idx
    return None


def _tap_expanded_menu_category(driver, category_label: str) -> bool:
    """펼침 패널에서 category_label 위치를 좌표로 계산해 탭한다.

    이 패널은 element 기반 탐색이 불가능해(위 설명 참고) 알려진 그리드
    좌표표를 쓴다. category_label이 이 표에(정확히든 접두사로든) 없으면
    False를 반환한다."""
    for row_idx, row in enumerate(_EXPANDED_MENU_GRID):
        col_idx = _match_grid_column(row, category_label)
        if col_idx is None:
            continue
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


def _wait_for_xpath(driver, xpath: str, timeout: float = _FIND_TIMEOUT_SEC):
    """xpath에 맞는 노드가 화면에 나타날 때까지 짧은 간격으로 폴링한다.

    실측 결과 restart_app 직후에는(특히 위치 변경 직후) 홈 화면이 완전히
    로딩되기까지 고정 시간보다 오래 걸리는 경우가 있었다 — 단발성 sleep
    대신 폴링으로 대기한다."""
    elapsed = 0.0
    while elapsed < timeout:
        elements = driver.find_elements("xpath", xpath)
        if elements:
            return elements
        time.sleep(_POLL_INTERVAL_SEC)
        elapsed += _POLL_INTERVAL_SEC
    return []


def _wait_for_text(driver, label: str, timeout: float = _FIND_TIMEOUT_SEC):
    """label과 정확히 일치하는 노드가 나타날 때까지 폴링한다."""
    return _wait_for_xpath(driver, f"//*[@text='{label}' or @content-desc='{label}']", timeout)


def navigate_to_category(driver, category_label: str) -> None:
    """홈 화면에서 카테고리 탭까지 이동해 category_label 탭을 클릭한다."""
    category_label = _resolve_category_label(category_label)
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


def scroll_and_collect(driver, max_scrolls: int = 30, target_name: str | None = None) -> list[str]:
    """리스트를 아래로 스크롤하며 매번 page_source를 누적 수집한다.

    target_name을 주면 매 스크롤 후 누적 소스에서 해당 가게를 찾았는지
    확인해 찾으면 즉시 멈춘다 — 스펙의 "가게를 찾은 시점에 스크린샷을
    저장한다" 요구사항을 만족하려면 호출부가 이 함수가 반환한 직후 곧바로
    스크린샷을 찍어야 화면에 가게가 보이는 증빙이 남는다(호출부 책임)."""
    from rank_finder import find_rank, parse_items  # 순환 의존 방지용 지역 임포트

    def _already_found(sources: list[str]) -> bool:
        return target_name is not None and find_rank(parse_items(sources), target_name)["rank"] is not None

    # 카테고리 화면에 막 진입한 직후에는 리스트 최상단 카드들이 아직 다
    # 렌더링되지 않은 상태일 수 있다 — 이 상태에서 바로 캡처하고 스크롤을
    # 시작하면, 화면엔 분명 보이는 상위권 가게가 accessibility tree
    # 캡처에는 안 잡히고, 이미 스크롤이 진행된 뒤라 다시는 캡처되지 않는
    # 문제가 실측으로 확인됐다(사용자가 상위권에 있는 걸 직접 확인했는데
    # 크롤러는 훨씬 아래에서 찾거나 아예 놓친 사례). 스크롤을 시작하기 전
    # 짧은 간격으로 여러 번 다시 캡처해 최상단이 완전히 안정될 시간을 준다.
    sources = [driver.page_source]
    for _ in range(_INITIAL_LOAD_SETTLE_CAPTURES - 1):
        if _already_found(sources):
            return sources
        time.sleep(_INITIAL_LOAD_SETTLE_INTERVAL_SEC)
        sources.append(driver.page_source)
    if _already_found(sources):
        return sources

    size = driver.get_window_size()
    # 카드 1개가 화면 높이의 대략 1/3(한 화면에 3장 정도 보임)이다. 카드
    # 높이보다 확실히 작은 폭으로 촘촘하게 스크롤해야 매 캡처가 겹쳐서
    # 누락이 안 생긴다(실측으로 확인됨) — slow_scroll_test.py로 카드 높이에
    # 근접한 32%까지 폭을 넓혀도 누락 없이 동작하는 걸 검증했다.
    start_y = int(size["height"] * 0.87)
    end_y = int(size["height"] * 0.55)
    x = int(size["width"] * 0.5)

    for _ in range(max_scrolls):
        _smooth_drag(driver, x, start_y, end_y)
        time.sleep(_SCROLL_SETTLE_WAIT_SEC)
        sources.append(driver.page_source)
        if _already_found(sources):
            break
    return sources
