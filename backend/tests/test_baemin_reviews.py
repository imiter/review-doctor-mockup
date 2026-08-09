from datetime import datetime

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review

_RAW_REVIEW = {
    "id": 2026080402827696,
    "rating": 5.0,
    "contents": "진짜 맛있어요 재주문할게요",
    "memberNickname": "먹보왕",
    "orderCount": 3,
    "menus": [{"name": "양념치킨"}],
    "createdAt": "2026-08-04T21:12:33+09:00",
    "displayStatus": "DISPLAY",
}

_SHOP_NO = 14804912
_REVIEWS_URL = (
    f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews"
    "?from=2026-02-11&to=2026-08-10&offset=0&limit=10"
)


def test_map_review_translates_baemin_fields_to_our_schema():
    mapped = map_review(_RAW_REVIEW, store_id=7, platform_id=1, platform_shop_no="14804912")
    assert mapped == {
        "external_review_id": 2026080402827696,
        "rating": 5,
        "content": "진짜 맛있어요 재주문할게요",
        "customer_nickname": "먹보왕",
        "customer_order_count": 3,
        "menu_summary": "양념치킨",
        "created_at": datetime.fromisoformat("2026-08-04T21:12:33+09:00"),
        "store_id": 7,
        "platform_id": 1,
        "platform_shop_no": "14804912",
        "status": "unanswered",
    }


def test_map_review_summarizes_multiple_menus():
    raw = {**_RAW_REVIEW, "menus": [{"name": "양념치킨"}, {"name": "콜라 1.25L"}]}
    mapped = map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")
    assert mapped["menu_summary"] == "양념치킨 외 1건"


def test_map_review_rounds_fractional_rating():
    raw = {**_RAW_REVIEW, "rating": 4.0}
    assert map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")["rating"] == 4


def test_map_review_handles_empty_content():
    raw = {**_RAW_REVIEW, "contents": ""}
    assert map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")["content"] == ""


class _FakeMoreButtonLocator:
    """`page.get_by_text("더보기", exact=True)`가 반환하는 Locator의 최소 흉내.
    실제 코드가 쓰는 부분만 흉내낸다: `.count()`, `.first`,
    `.scroll_into_view_if_needed()`, `.click(timeout=...)`. 기본값은 "버튼이
    항상 있고 클릭은 항상 성공하지만 진행은 안 남"이라 대부분의 응답 캡처
    테스트가 이 기본값을 그대로 써도 무진행 조기 종료로 부작용 없이 끝난다.
    """

    def __init__(self):
        self.present = True
        self.click_calls = 0
        self.click_raises: Exception | None = None

    def count(self):
        return 1 if self.present else 0

    @property
    def first(self):
        return self

    def scroll_into_view_if_needed(self):
        pass

    def click(self, timeout=None):
        self.click_calls += 1
        if self.click_raises:
            raise self.click_raises


class _FakeResponse:
    def __init__(self, url: str, status: int, body: dict | None = None):
        self.url = url
        self.status = status
        self._body = body

    def json(self):
        return self._body


class _FakePage:
    """실제 코드가 기대하는 page 인터페이스의 최소 흉내:
    - on(event, handler) / remove_listener(event, handler): response 리스너 등록/해제.
      테스트는 등록된 handler를 직접 호출해 응답 이벤트를 시뮬레이션한다.
    - goto(url): URL만 기록.
    - wait_for_timeout(ms): no-op.
    - get_by_text("더보기", exact=True): "더보기" 버튼 Locator 흉내(_FakeMoreButtonLocator)를 반환.
    """

    def __init__(self, goto_raises: Exception | None = None):
        self._handlers: dict[str, object] = {}
        self.goto_calls: list[str] = []
        self.more_button = _FakeMoreButtonLocator()
        self._goto_raises = goto_raises

    def on(self, event, handler):
        self._handlers[event] = handler

    def remove_listener(self, event, handler):
        # 실제 Playwright처럼 handler 정체가 일치할 때만 제거한다 — 등록된
        # 핸들러와 다른 객체로 remove_listener를 호출하면 아무 일도 일어나지
        # 않아야, "removes listener when done" 테스트가 진짜로 올바른
        # handler를 넘겼는지 검증하는 의미를 가진다.
        if self._handlers.get(event) is handler:
            del self._handlers[event]

    def goto(self, url):
        self.goto_calls.append(url)
        if self._goto_raises:
            raise self._goto_raises

    def wait_for_timeout(self, ms):
        pass

    def get_by_text(self, text, exact=False):
        return self.more_button


def _review_response(reviews: list[dict], offset: int = 0) -> _FakeResponse:
    url = (
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews"
        f"?from=2026-02-11&to=2026-08-10&offset={offset}&limit=10"
    )
    return _FakeResponse(url, 200, {"reviews": reviews, "next": True})


def test_fetch_all_reviews_captures_single_organic_response():
    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert result[0]["id"] == _RAW_REVIEW["id"]
    assert p.goto_calls == [f"https://self.baemin.com/shops/{_SHOP_NO}/reviews"]


def test_fetch_all_reviews_combines_two_organic_prefetch_pages():
    second_review = {**_RAW_REVIEW, "id": 999}

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](_review_response([_RAW_REVIEW], offset=0))
                self._handlers["response"](_review_response([second_review], offset=10))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    ids = {r["id"] for r in result}
    assert ids == {_RAW_REVIEW["id"], 999}


def test_fetch_all_reviews_deduplicates_by_id_across_responses():
    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](_review_response([_RAW_REVIEW]))
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1


def test_fetch_all_reviews_ignores_other_endpoints():
    # 쿼리 스트링을 붙여야 경로 기반 매칭이 실제로 걸러내는지 검증된다 —
    # 쿼리 스트링 없는 URL은 예전 prefix 매칭 버그도 못 잡았을 adversarial하지
    # 않은 케이스다.
    stat_response = _FakeResponse(
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews/stat"
        "?from=2026-01-01",
        200,
        {"reviews": [_RAW_REVIEW]},
    )
    count_response = _FakeResponse(
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews/count"
        "?from=2026-01-01",
        200,
        {"count": 42},
    )

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](stat_response)
                self._handlers["response"](count_response)
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    # stat/count 응답에도 "reviews" 키가 있었지만 경로가 다르므로 수집되지
    # 않아야 한다 — 실제로 관측된 리뷰 목록 응답의 리뷰 1건만 남는다.
    assert len(result) == 1
    assert result[0]["id"] == _RAW_REVIEW["id"]


def test_fetch_all_reviews_matches_review_list_response_with_reordered_query_params():
    # 파라미터 순서가 바뀌어도(offset이 from보다 먼저 와도) 경로만 같으면
    # 매칭돼야 한다 — 문자열 prefix 매칭이었다면 이 케이스는 놓쳤을 것이다.
    reordered_url = (
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews"
        "?offset=0&limit=10&to=2026-08-10&from=2026-02-11"
    )
    response = _FakeResponse(reordered_url, 200, {"reviews": [_RAW_REVIEW]})

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](response)

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert result[0]["id"] == _RAW_REVIEW["id"]


def test_fetch_all_reviews_ignores_response_for_different_shop_no():
    other_shop_review_id = 555
    other_shop_response = _FakeResponse(
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO + 1}/reviews"
        "?from=2026-02-11&to=2026-08-10&offset=0&limit=10",
        200,
        {"reviews": [{**_RAW_REVIEW, "id": other_shop_review_id}]},
    )

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](other_shop_response)
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    ids = {r["id"] for r in result}
    assert ids == {_RAW_REVIEW["id"]}
    assert other_shop_review_id not in ids


def test_fetch_all_reviews_skips_non_200_review_list_response():
    failed_response = _FakeResponse(_REVIEWS_URL, 500, {"reviews": [_RAW_REVIEW]})

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](failed_response)
                self._handlers["response"](_review_response([{**_RAW_REVIEW, "id": 1}]))

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert result[0]["id"] == 1


def test_fetch_all_reviews_raises_on_navigation_failure():
    page = _FakePage(goto_raises=RuntimeError("network error"))
    with pytest.raises(BaeminScrapeError):
        fetch_all_reviews(page, shop_no=_SHOP_NO)


def test_fetch_all_reviews_raises_when_review_list_endpoint_never_observed():
    # 리뷰 목록 엔드포인트에 대한 응답을 단 한 번도 보지 못했다면 — URL 패턴이
    # 바뀌었거나, 클라이언트 사이드 404거나, 인증이 만료됐거나 — 이건 "리뷰
    # 0건"과 절대 같은 의미가 아니므로 조용히 빈 리스트를 반환해서는 안 된다.
    page = _FakePage()
    with pytest.raises(BaeminScrapeError, match="한 번도"):
        fetch_all_reviews(page, shop_no=_SHOP_NO)


def test_fetch_all_reviews_returns_empty_list_when_shop_genuinely_has_no_reviews():
    # 엔드포인트 응답을 실제로 관측했고, 그 응답의 reviews가 빈 배열이면
    # 매장에 리뷰가 아직 없다는 정상 상태이므로 에러가 아니다.
    empty_response = _FakeResponse(_REVIEWS_URL, 200, {"reviews": []})

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](empty_response)

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)
    assert result == []


def test_fetch_all_reviews_does_not_raise_when_endpoint_observed_only_via_error_status():
    # 200이 아니어도(401/500 등) 리뷰 목록 엔드포인트 자체는 응답을 준 것이므로
    # "한 번도 확인하지 못함" 에러를 던지면 안 된다 — 이 케이스는 다운스트림
    # 문제(인증 만료 등)이지 엔드포인트를 놓친 게 아니다.
    error_response = _FakeResponse(_REVIEWS_URL, 401, None)

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](error_response)

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)
    assert result == []


def test_fetch_all_reviews_removes_response_listener_when_done():
    # 리스너 제거 자체를 검증하는 테스트이므로, 새로 추가된 "한 번도 못 봄"
    # 에러 경로와는 분리해 정상 성공 경로(관측됨 + 리뷰 있음)로 확인한다.
    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p._fired = False
    fetch_all_reviews(p, shop_no=_SHOP_NO)
    assert "response" not in p._handlers


def test_fetch_all_reviews_stops_load_more_after_two_consecutive_no_progress_clicks():
    # 초기 로드에서 리뷰 1건을 관측시켜 엔드포인트를 확인시킨 뒤, 이후 "더보기"
    # 클릭 대기에서는 새 응답을 전혀 발생시키지 않는다(진행 없음). "연속 2번
    # 무진행" 조기 종료 규칙에 따라 정확히 2번만 클릭해야 한다. 버튼 자체는
    # 계속 존재한다고 흉내낸다(_FakeMoreButtonLocator 기본값 present=True) —
    # 실 계정 관찰상 "더보기"가 끝에서도 사라지지 않았던 것과 같은 조건이라,
    # count()==0이 아니라 무진행 카운터가 실제로 종료시켰는지를 검증한다.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert p.more_button.click_calls == 2


def test_fetch_all_reviews_hits_hard_cap_when_every_click_makes_progress():
    # 매 "더보기" 클릭마다 새 리뷰가 하나씩 도착해 진행이 계속되는 상황을
    # 흉내낸다. 무진행 조기 종료 조건이 절대 걸리지 않아도 하드 캡(30회)에서
    # 반드시 멈춰야 한다.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            self._handlers["response"](
                _review_response([{**_RAW_REVIEW, "id": 9_000_000 + self._wait_count}])
            )

    p = _Page()
    fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert p.more_button.click_calls == 30


def test_fetch_all_reviews_stops_early_when_more_button_disappears():
    # 첫 클릭은 진행이 있었다고(새 리뷰 1건) 흉내낸 다음 "더보기" 버튼이
    # 사라지게(count() == 0) 만든다. 무진행 카운터는 아직 한 번도 증가하지
    # 않았고 하드 캡(30)에도 한참 못 미치므로, 버튼 소멸 자체가 조기 종료를
    # 일으켰는지를 검증한다.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([_RAW_REVIEW]))
            elif self._wait_count == 2:
                self._handlers["response"](
                    _review_response([{**_RAW_REVIEW, "id": 2}])
                )
                self.more_button.present = False

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 2
    assert p.more_button.click_calls == 1


def test_fetch_all_reviews_stops_when_more_button_click_times_out():
    # 클릭 자체가 PlaywrightTimeoutError로 실패하면(버튼이 안 보이거나 다른
    # 요소에 가려진 경우 등) 더 시도하지 않고 조용히 루프를 빠져나와야 한다 —
    # 이미 수집된 리뷰는 그대로 반환한다.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    p.more_button.click_raises = PlaywrightTimeoutError("click timed out")
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert p.more_button.click_calls == 1
