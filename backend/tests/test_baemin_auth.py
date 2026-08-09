"""baemin_auth.login()의 리소스 정리 회귀 테스트.

실 배민 계정/네트워크 없이도 확인 가능한 부분만 다룬다: 로그인 흐름 중
BaeminLoginError가 아닌 예외(예: page.goto의 네트워크 오류)가 나도
browser.close()/playwright.stop()이 항상 호출되고, 호출자에게는
BaeminLoginError 하나로 감싸져서 전달되는지를 검증한다. 실제 셀렉터 동작이나
봉 탐지 우회 자체는 pytest로 재현할 수 없어 수동 검증(Task 3 report 참고)으로만
확인한다.
"""

from unittest.mock import MagicMock, patch

import pytest

from scrapers.baemin_auth import BaeminLoginError, _discover_all_shops, login


def _fake_option(value, text):
    option = MagicMock()
    option.get_attribute.return_value = value
    option.inner_text.return_value = text
    return option


def test_login_cleans_up_browser_and_playwright_on_unexpected_error():
    fake_playwright = MagicMock()
    fake_browser = MagicMock()
    fake_context = MagicMock()
    fake_page = MagicMock()

    fake_playwright.chromium.launch.return_value = fake_browser
    fake_browser.new_context.return_value = fake_context
    fake_context.new_page.return_value = fake_page
    # goto에서 터지는 예외는 BaeminLoginError가 아니다 — 수정 전에는 이런
    # 경로가 정리 없이 그대로 새 나갔다(실제로 봉 탐지 차단으로 인한
    # get_by_test_id("id").fill() 타임아웃에서 재현됐던 상황과 같은 종류).
    fake_page.goto.side_effect = RuntimeError("network boom")

    with patch("scrapers.baemin_auth.sync_playwright") as mock_sync_playwright:
        mock_sync_playwright.return_value.start.return_value = fake_playwright

        with pytest.raises(BaeminLoginError):
            login("test-id", "test-pw", headless=True)

    fake_browser.close.assert_called_once()
    fake_playwright.stop.assert_called_once()


def test_discover_all_shops_returns_every_real_option_and_skips_placeholder():
    """실 계정에서는 로그인 한 번에 여러 브랜드가 매장 선택 <select>에 딸려
    나온다(4개 브랜드 계정으로 실측 확인). 옵션 중 값이 숫자가 아닌
    플레이스홀더("선택하세요" 같은)는 매장이 아니므로 제외돼야 하고, 나머지는
    <select>에 나온 순서 그대로 (shop_no, shop_name) 튜플로 반환돼야 한다."""
    fake_page = MagicMock()

    shop_select = MagicMock()
    options = [
        _fake_option("", "선택하세요"),  # 플레이스홀더 — 빈 value는 건너뛰어야 함
        _fake_option("11111", "브랜드A"),
        _fake_option("22222", "브랜드B"),
        _fake_option("33333", "브랜드C"),
    ]
    shop_select.locator.return_value.all.return_value = options
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select
    fake_page.get_by_role.side_effect = lambda role, name=None: (
        combobox_locator if role == "combobox" else MagicMock()
    )

    shops = _discover_all_shops(fake_page)

    assert shops == [(11111, "브랜드A"), (22222, "브랜드B"), (33333, "브랜드C")]


def test_discover_all_shops_skips_non_digit_placeholder_value():
    """value가 비어있지 않지만 숫자가 아닌 플레이스홀더("선택" 등)도 매장이
    아니므로 제외돼야 한다 — 빈 문자열 케이스와는 다른 분기(`not value.isdigit()`)를
    탄다."""
    fake_page = MagicMock()

    shop_select = MagicMock()
    options = [
        _fake_option("선택", "선택하세요"),
        _fake_option("99999", "유일한매장"),
    ]
    shop_select.locator.return_value.all.return_value = options
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select
    fake_page.get_by_role.side_effect = lambda role, name=None: (
        combobox_locator if role == "combobox" else MagicMock()
    )

    shops = _discover_all_shops(fake_page)

    assert shops == [(99999, "유일한매장")]


def test_login_does_not_close_session_resources_on_success():
    fake_playwright = MagicMock()
    fake_browser = MagicMock()
    fake_context = MagicMock()
    fake_page = MagicMock()

    fake_playwright.chromium.launch.return_value = fake_browser
    fake_browser.new_context.return_value = fake_context
    fake_context.new_page.return_value = fake_page

    # "홈으로 이동" 복구 클릭은 없어도 되는 경로(바로 로그인 폼이 뜨는 경우)로
    # 시뮬레이션한다. wait_for_url은 성공(예외 없음)으로 통과시킨다.
    fake_page.wait_for_url.return_value = None

    shop_select = MagicMock()
    option = MagicMock()
    option.get_attribute.return_value = "14804318"
    option.inner_text.return_value = "테스트 매장"
    shop_select.locator.return_value.all.return_value = [option]
    # 실제 코드는 page.get_by_role("combobox").nth(1)로 select를 얻는다 —
    # combobox 쪽 locator는 별도 MagicMock으로 두고 .nth(1)이 그 select를
    # 반환하도록 연결해야 체인이 실제 코드와 맞는다.
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select
    fake_page.get_by_role.side_effect = lambda role, name=None: (
        combobox_locator if role == "combobox" else MagicMock()
    )

    with patch("scrapers.baemin_auth.sync_playwright") as mock_sync_playwright:
        mock_sync_playwright.return_value.start.return_value = fake_playwright

        session = login("test-id", "test-pw", headless=True)

    assert session.shop_no == 14804318
    assert session.shop_name == "테스트 매장"
    # 성공 시에는 login() 스코프에서 정리하지 않는다 — 세션을 다 쓴 뒤
    # 호출자가 session.close()로 닫는 게 계약이다.
    fake_browser.close.assert_not_called()
    fake_playwright.stop.assert_not_called()
    # 로그인 성공 후 매장 탐색 전에 프로모션 모달을 방어적으로 닫는지 확인한다
    # (실 모달 유무는 이 mock으로 검증 불가 — Escape가 항상 눌리는지만 확인).
    fake_page.keyboard.press.assert_called_once_with("Escape")


def test_login_sets_legacy_shop_fields_from_first_of_multiple_shops():
    """실 계정(4개 브랜드)처럼 <select>에 매장이 여러 개 나오는 경우에도
    BaeminSession.shop_no/shop_name은 하위 호환 필드로서 shops[0]과 정확히
    같아야 한다 — review_sync.py가 여러 매장을 순회하는 것과 별개로, 최초
    로그인 시 connections.platform_store_id에 저장되는 대표 매장은 항상 첫
    번째 매장이라는 계약을 명시적으로 고정한다."""
    fake_playwright = MagicMock()
    fake_browser = MagicMock()
    fake_context = MagicMock()
    fake_page = MagicMock()

    fake_playwright.chromium.launch.return_value = fake_browser
    fake_browser.new_context.return_value = fake_context
    fake_context.new_page.return_value = fake_page
    fake_page.wait_for_url.return_value = None

    shop_select = MagicMock()
    options = [
        _fake_option("11111", "브랜드A"),
        _fake_option("22222", "브랜드B"),
        _fake_option("33333", "브랜드C"),
    ]
    shop_select.locator.return_value.all.return_value = options
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select
    fake_page.get_by_role.side_effect = lambda role, name=None: (
        combobox_locator if role == "combobox" else MagicMock()
    )

    with patch("scrapers.baemin_auth.sync_playwright") as mock_sync_playwright:
        mock_sync_playwright.return_value.start.return_value = fake_playwright

        session = login("test-id", "test-pw", headless=True)

    assert session.shops == [(11111, "브랜드A"), (22222, "브랜드B"), (33333, "브랜드C")]
    assert (session.shop_no, session.shop_name) == session.shops[0]
    assert session.shop_no == 11111
    assert session.shop_name == "브랜드A"
