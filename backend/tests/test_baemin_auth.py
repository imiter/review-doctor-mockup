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

from scrapers.baemin_auth import BaeminLoginError, login


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
