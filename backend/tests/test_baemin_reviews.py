from datetime import datetime

import pytest

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
    mapped = map_review(_RAW_REVIEW, store_id=7, platform_id=1)
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
        "status": "unanswered",
    }


def test_map_review_summarizes_multiple_menus():
    raw = {**_RAW_REVIEW, "menus": [{"name": "양념치킨"}, {"name": "콜라 1.25L"}]}
    mapped = map_review(raw, store_id=7, platform_id=1)
    assert mapped["menu_summary"] == "양념치킨 외 1건"


def test_map_review_rounds_fractional_rating():
    raw = {**_RAW_REVIEW, "rating": 4.0}
    assert map_review(raw, store_id=7, platform_id=1)["rating"] == 4


def test_map_review_handles_empty_content():
    raw = {**_RAW_REVIEW, "contents": ""}
    assert map_review(raw, store_id=7, platform_id=1)["content"] == ""


class _FakeMouse:
    def __init__(self):
        self.wheel_calls: list[tuple[int, int]] = []

    def wheel(self, x, y):
        self.wheel_calls.append((x, y))


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
    - mouse.wheel(x, y): 호출만 기록.
    """

    def __init__(self, goto_raises: Exception | None = None):
        self._handlers: dict[str, object] = {}
        self.goto_calls: list[str] = []
        self.mouse = _FakeMouse()
        self._goto_raises = goto_raises

    def on(self, event, handler):
        self._handlers[event] = handler

    def remove_listener(self, event, handler):
        self._handlers.pop(event, None)

    def goto(self, url):
        self.goto_calls.append(url)
        if self._goto_raises:
            raise self._goto_raises

    def wait_for_timeout(self, ms):
        pass


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
    stat_response = _FakeResponse(
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews/stat",
        200,
        {"reviews": [_RAW_REVIEW]},
    )
    count_response = _FakeResponse(
        f"https://self-api.baemin.com/v1/review/shops/{_SHOP_NO}/reviews/count",
        200,
        {"count": 42},
    )

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not self._fired:
                self._fired = True
                self._handlers["response"](stat_response)
                self._handlers["response"](count_response)

    p = _Page()
    p._fired = False
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert result == []


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


def test_fetch_all_reviews_returns_empty_list_without_raising_when_no_reviews_captured():
    page = _FakePage()
    result = fetch_all_reviews(page, shop_no=_SHOP_NO)
    assert result == []


def test_fetch_all_reviews_removes_response_listener_when_done():
    page = _FakePage()
    fetch_all_reviews(page, shop_no=_SHOP_NO)
    assert "response" not in page._handlers
