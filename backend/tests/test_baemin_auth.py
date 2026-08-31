"""baemin_auth.login()의 리소스 정리 회귀 테스트.

실 배민 계정/네트워크 없이도 확인 가능한 부분만 다룬다: 로그인 흐름 중
BaeminLoginError가 아닌 예외(예: page.goto의 네트워크 오류)가 나도
browser.close()/playwright.stop()이 항상 호출되고, 호출자에게는
BaeminLoginError 하나로 감싸져서 전달되는지를 검증한다. 실제 셀렉터 동작이나
봉 탐지 우회 자체는 pytest로 재현할 수 없어 수동 검증(Task 3 report 참고)으로만
확인한다.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scrapers.baemin_auth import BaeminLoginError, _discover_all_shops, capture_failure_diagnostics, login


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


def test_discover_all_shops_retries_read_once_when_first_read_is_empty():
    """첫 <option>이 DOM에 붙는 시점과 실제 매장 옵션들까지 다 채워지는 시점
    사이에 간격이 있어 첫 읽기가 빈 리스트로 나온 실 계정 사례가 재현됐다
    (2026-08-31, 4개 브랜드 계정). 바로 실패시키지 않고 한 번 더 기다렸다가
    다시 읽어서 성공해야 한다."""
    fake_page = MagicMock()

    review_button = MagicMock()
    shop_select = MagicMock()
    shop_select.locator.return_value.all.side_effect = [
        [],  # 첫 읽기 — 아직 옵션이 안 채워짐
        [_fake_option("14804318", "테스트 매장")],  # 재시도 — 채워짐
    ]
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select

    def _get_by_role(role, name=None):
        if role == "button":
            return review_button
        if role == "combobox":
            return combobox_locator
        return MagicMock()

    fake_page.get_by_role.side_effect = _get_by_role

    shops = _discover_all_shops(fake_page)

    assert shops == [(14804318, "테스트 매장")]
    fake_page.wait_for_timeout.assert_any_call(1_500)


def test_discover_all_shops_raises_when_retry_read_still_empty():
    """재시도까지 다 빈 리스트면(총 _SHOP_LIST_READ_ATTEMPTS번) 진짜 실패로
    취급해 에러를 던져야 한다 — 무한정 재시도하며 조용히 매달리면 안 된다."""
    fake_page = MagicMock()

    review_button = MagicMock()
    shop_select = MagicMock()
    shop_select.locator.return_value.all.side_effect = [[], [], []]
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select

    def _get_by_role(role, name=None):
        if role == "button":
            return review_button
        if role == "combobox":
            return combobox_locator
        return MagicMock()

    fake_page.get_by_role.side_effect = _get_by_role

    with pytest.raises(BaeminLoginError, match="매장 목록을 확인하지 못했습니다"):
        _discover_all_shops(fake_page)


def test_discover_all_shops_retries_after_option_read_timeout():
    """리스트 자체는 채워졌어도 특정 옵션의 inner_text() 읽기가 타임아웃나는
    경우가 실 계정에서 재현됐다(2026-08-31, 블랙닭갈비 캠페인 순위 확인
    시도 중 — 매번 다른 옵션 인덱스에서 재현돼 특정 옵션이 아니라 접근성
    트리 갱신 자체가 늦는 것으로 보인다). 빈 리스트뿐 아니라 이 타임아웃도
    같은 재시도 루프로 흡수해야 한다."""
    fake_page = MagicMock()

    review_button = MagicMock()
    shop_select = MagicMock()
    shop_select.locator.return_value.all.side_effect = [
        PlaywrightTimeoutError("Locator.inner_text: Timeout 30000ms exceeded."),
        [_fake_option("14804914", "블랙닭갈비 노원당고개점")],
    ]
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select

    def _get_by_role(role, name=None):
        if role == "button":
            return review_button
        if role == "combobox":
            return combobox_locator
        return MagicMock()

    fake_page.get_by_role.side_effect = _get_by_role

    shops = _discover_all_shops(fake_page)

    assert shops == [(14804914, "블랙닭갈비 노원당고개점")]


def test_discover_all_shops_raises_after_exhausting_all_attempts_on_timeout():
    fake_page = MagicMock()

    review_button = MagicMock()
    shop_select = MagicMock()
    shop_select.locator.return_value.all.side_effect = PlaywrightTimeoutError("timeout")
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select

    def _get_by_role(role, name=None):
        if role == "button":
            return review_button
        if role == "combobox":
            return combobox_locator
        return MagicMock()

    fake_page.get_by_role.side_effect = _get_by_role

    with pytest.raises(BaeminLoginError, match="매장 목록을 확인하지 못했습니다"):
        _discover_all_shops(fake_page)


def test_discover_all_shops_retries_click_once_after_late_modal(monkeypatch):
    """로그인 직후 Escape 한 번으로는 못 닫는, 뒤늦게 뜨는 프로모션 모달이
    실 계정에서 재현됐다(2026-08-19, "리뷰관리" 버튼 클릭이 30초 타임아웃).
    첫 클릭이 타임아웃되면 Escape를 한 번 더 누르고 재시도해야 한다."""
    fake_page = MagicMock()

    review_button = MagicMock()
    review_button.click.side_effect = [PlaywrightTimeoutError("timeout"), None]
    shop_select = MagicMock()
    shop_select.locator.return_value.all.return_value = [_fake_option("14804318", "테스트 매장")]
    combobox_locator = MagicMock()
    combobox_locator.nth.return_value = shop_select

    def _get_by_role(role, name=None):
        if role == "button":
            return review_button
        if role == "combobox":
            return combobox_locator
        return MagicMock()

    fake_page.get_by_role.side_effect = _get_by_role

    shops = _discover_all_shops(fake_page)

    assert shops == [(14804318, "테스트 매장")]
    assert review_button.click.call_count == 2
    fake_page.keyboard.press.assert_called_once_with("Escape")


def test_discover_all_shops_raises_when_retry_also_times_out():
    fake_page = MagicMock()

    review_button = MagicMock()
    review_button.click.side_effect = PlaywrightTimeoutError("timeout")
    fake_page.get_by_role.side_effect = lambda role, name=None: (
        review_button if role == "button" else MagicMock()
    )

    with pytest.raises(PlaywrightTimeoutError):
        _discover_all_shops(fake_page)

    assert review_button.click.call_count == 2


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


def test_capture_failure_diagnostics_detects_bot_block_screen(tmp_path, monkeypatch):
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com/shops/123/stat"
    fake_page.get_by_text.return_value.count.return_value = 1  # 차단 문구 발견됨

    summary = capture_failure_diagnostics(fake_page, "shop-stats-123")

    assert "봇차단화면=감지됨" in summary
    fake_page.get_by_text.assert_called_once_with("비정상 동작이 감지되어")


def test_capture_failure_diagnostics_reports_no_block_screen(tmp_path, monkeypatch):
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com/shops/123/stat"
    fake_page.get_by_text.return_value.count.return_value = 0

    summary = capture_failure_diagnostics(fake_page, "shop-stats-123")

    assert "봇차단화면=미감지" in summary


def test_capture_failure_diagnostics_includes_current_url(tmp_path, monkeypatch):
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com/some/blocked/page"
    fake_page.get_by_text.return_value.count.return_value = 0

    summary = capture_failure_diagnostics(fake_page, "label")

    assert "URL=https://self.baemin.com/some/blocked/page" in summary


def test_capture_failure_diagnostics_saves_screenshot_under_diagnostics_dir(tmp_path, monkeypatch):
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com/shops/123/stat"
    fake_page.get_by_text.return_value.count.return_value = 0

    summary = capture_failure_diagnostics(fake_page, "shop-stats-123")

    fake_page.screenshot.assert_called_once()
    call_kwargs = fake_page.screenshot.call_args.kwargs
    assert call_kwargs["full_page"] is True
    assert str(tmp_path) in call_kwargs["path"]
    assert "shop-stats-123" in call_kwargs["path"]
    assert "스크린샷=" in summary
    assert "저장 실패" not in summary


def test_capture_failure_diagnostics_survives_screenshot_failure(tmp_path, monkeypatch):
    # 스크린샷 저장 자체가 실패해도(디스크 문제 등) 예외를 삼키고 요약에만
    # 남겨야 한다 — 진단 시도가 원래 에러를 가리면 안 된다.
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com/shops/123/stat"
    fake_page.get_by_text.return_value.count.return_value = 0
    fake_page.screenshot.side_effect = RuntimeError("disk full")

    summary = capture_failure_diagnostics(fake_page, "shop-stats-123")

    assert "스크린샷=저장 실패" in summary


def test_capture_failure_diagnostics_survives_url_and_get_by_text_failures(tmp_path, monkeypatch):
    # page.url 접근이나 get_by_text 자체가 예외를 던져도(페이지가 이미 닫혔거나
    # 크래시한 극단적인 경우) 전체 함수가 죽지 않고 "알 수 없음"으로 남긴다.
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    type(fake_page).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("page closed")))
    fake_page.get_by_text.side_effect = RuntimeError("page closed")

    summary = capture_failure_diagnostics(fake_page, "shop-stats-123")

    assert "URL=알 수 없음" in summary
    assert "봇차단화면=미감지" in summary


def test_capture_failure_diagnostics_sanitizes_label_for_filename(tmp_path, monkeypatch):
    import scrapers.baemin_auth as auth_module

    monkeypatch.setattr(auth_module, "_DIAGNOSTICS_DIR", tmp_path)
    fake_page = MagicMock()
    fake_page.url = "https://self.baemin.com"
    fake_page.get_by_text.return_value.count.return_value = 0

    capture_failure_diagnostics(fake_page, "click-metrics-14804912/weird name")

    call_kwargs = fake_page.screenshot.call_args.kwargs
    assert "/" not in Path(call_kwargs["path"]).name
    assert " " not in Path(call_kwargs["path"]).name
