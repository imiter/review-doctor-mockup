from datetime import datetime

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scrapers.baemin_reviews import (
    _INITIAL_LOAD_WAIT_MS,
    _LOAD_MORE_WAIT_MS,
    BaeminScrapeError,
    _consecutive_known_count,
    extract_image_urls,
    extract_owner_reply,
    fetch_all_reviews,
    map_review,
)

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

# 실 계정(치밥대장)에서 확인한 실제 사진 첨부 리뷰의 images 구조.
_REVIEW_IMAGE = {
    "id": 2026082203004809,
    "imageUrl": "https://bmreview.cdn.baemin.com/bmreview-qh2e/i/2026/8/22/01m0mxhc2dxcrgzd563drdf0wc.jpg",
    "displayStatus": "DISPLAY",
    "sequence": 1,
    "createdAt": "2026-08-22T23:21:01.945551",
    "modifiedAt": "2026-08-22T23:21:01.945551",
    "blockMessage": "",
}

# 실 계정(치밥대장)에서 확인한 실제 답글 달린 리뷰의 comments 구조.
_OWNER_REPLY_COMMENT = {
    "id": 2026072903000545,
    "managerNo": 251001000193,
    "managerNickname": "사장님",
    "contents": "안녕하세요, 맛있게 드셨다니 정말 기쁩니다! 감사합니다.",
    "displayType": "CEO",
    "displayStatus": "DISPLAY",
    "createdDate": "지난 달",
    "createdAt": "2026-07-30T19:03:18.416481",
    "modifiable": False,
    "blockType": "NONE",
    "blockMessage": "",
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
        "image_urls": [],
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


def test_extract_owner_reply_returns_none_when_no_comments():
    assert extract_owner_reply(_RAW_REVIEW) is None
    assert extract_owner_reply({**_RAW_REVIEW, "comments": []}) is None


def test_extract_owner_reply_returns_content_and_timestamp():
    raw = {**_RAW_REVIEW, "comments": [_OWNER_REPLY_COMMENT]}
    result = extract_owner_reply(raw)
    assert result == (
        "안녕하세요, 맛있게 드셨다니 정말 기쁩니다! 감사합니다.",
        datetime.fromisoformat("2026-07-30T19:03:18.416481"),
    )


def test_extract_owner_reply_ignores_hidden_comment():
    hidden = {**_OWNER_REPLY_COMMENT, "displayStatus": "HIDDEN"}
    raw = {**_RAW_REVIEW, "comments": [hidden]}
    assert extract_owner_reply(raw) is None


def test_extract_owner_reply_uses_first_display_comment_when_multiple():
    second = {**_OWNER_REPLY_COMMENT, "id": 999, "contents": "두 번째 답글", "createdAt": "2026-08-01T00:00:00"}
    raw = {**_RAW_REVIEW, "comments": [_OWNER_REPLY_COMMENT, second]}
    content, _ = extract_owner_reply(raw)
    assert content == _OWNER_REPLY_COMMENT["contents"]


def test_map_review_status_is_answered_when_owner_already_replied():
    raw = {**_RAW_REVIEW, "comments": [_OWNER_REPLY_COMMENT]}
    mapped = map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")
    assert mapped["status"] == "answered"


def test_map_review_status_is_unanswered_when_only_hidden_comment_present():
    hidden = {**_OWNER_REPLY_COMMENT, "displayStatus": "HIDDEN"}
    raw = {**_RAW_REVIEW, "comments": [hidden]}
    mapped = map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")
    assert mapped["status"] == "unanswered"


def test_map_review_handles_empty_content():
    raw = {**_RAW_REVIEW, "contents": ""}
    assert map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")["content"] == ""


def test_extract_image_urls_returns_empty_list_when_no_images():
    assert extract_image_urls(_RAW_REVIEW) == []
    assert extract_image_urls({**_RAW_REVIEW, "images": []}) == []


def test_extract_image_urls_returns_display_image_urls():
    raw = {**_RAW_REVIEW, "images": [_REVIEW_IMAGE]}
    assert extract_image_urls(raw) == [_REVIEW_IMAGE["imageUrl"]]


def test_extract_image_urls_ignores_hidden_image():
    hidden = {**_REVIEW_IMAGE, "displayStatus": "HIDDEN"}
    raw = {**_RAW_REVIEW, "images": [hidden]}
    assert extract_image_urls(raw) == []


def test_extract_image_urls_orders_by_sequence():
    first = {**_REVIEW_IMAGE, "sequence": 2, "imageUrl": "https://bmreview.cdn.baemin.com/second.jpg"}
    second = {**_REVIEW_IMAGE, "sequence": 1, "imageUrl": "https://bmreview.cdn.baemin.com/first.jpg"}
    raw = {**_RAW_REVIEW, "images": [first, second]}
    assert extract_image_urls(raw) == [
        "https://bmreview.cdn.baemin.com/first.jpg",
        "https://bmreview.cdn.baemin.com/second.jpg",
    ]


def test_map_review_includes_image_urls():
    raw = {**_RAW_REVIEW, "images": [_REVIEW_IMAGE]}
    mapped = map_review(raw, store_id=7, platform_id=1, platform_shop_no="14804912")
    assert mapped["image_urls"] == [_REVIEW_IMAGE["imageUrl"]]


def test_consecutive_known_count_counts_trailing_known_ids():
    # 끝에서부터(가장 최근 도착 순) known인 개수만 센다.
    assert _consecutive_known_count([1, 2, 3, 4, 5], {3, 4, 5}) == 3


def test_consecutive_known_count_stops_at_first_unknown_from_the_end():
    # 끝에서 세다가 모르는 id를 만나면 거기서 멈춘다 — 더 앞쪽에 known이
    # 남아있어도 세지 않는다.
    assert _consecutive_known_count([1, 2, 3, 4, 5], {1, 2, 4, 5}) == 2


def test_consecutive_known_count_returns_zero_when_last_is_unknown():
    assert _consecutive_known_count([1, 2, 3], {1, 2}) == 0


def test_consecutive_known_count_returns_zero_for_empty_list():
    assert _consecutive_known_count([], {1, 2, 3}) == 0


def test_consecutive_known_count_counts_everything_when_all_known():
    assert _consecutive_known_count([1, 2, 3], {1, 2, 3}) == 3


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


class _FakeBackdropLocator:
    """`page.get_by_test_id("backdrop")`가 반환하는 Locator의 최소 흉내.
    `.count()`만 있으면 된다 — 실제 코드는 개수만 확인하고 클릭/조작하지 않는다.
    """

    def __init__(self):
        self.present = False

    def count(self):
        return 1 if self.present else 0


class _FakeKeyboard:
    def __init__(self):
        self.press_calls: list[str] = []

    def press(self, key):
        self.press_calls.append(key)


class _FakePage:
    """실제 코드가 기대하는 page 인터페이스의 최소 흉내:
    - on(event, handler) / remove_listener(event, handler): response 리스너 등록/해제.
      테스트는 등록된 handler를 직접 호출해 응답 이벤트를 시뮬레이션한다.
    - goto(url): URL만 기록.
    - wait_for_timeout(ms): no-op.
    - get_by_text("더보기", exact=True): "더보기" 버튼 Locator 흉내(_FakeMoreButtonLocator)를 반환.
    - get_by_test_id("backdrop"): 프로모션 모달 backdrop Locator 흉내(_FakeBackdropLocator)를 반환.
      기본값은 present=False(backdrop 없음)라 대부분의 테스트에 부작용이 없다.
    - keyboard.press("Escape"): backdrop을 닫는 동작의 흉내(_FakeKeyboard).
    """

    def __init__(self, goto_raises: Exception | None = None):
        self._handlers: dict[str, object] = {}
        self.goto_calls: list[str] = []
        self.more_button = _FakeMoreButtonLocator()
        self.backdrop = _FakeBackdropLocator()
        self.keyboard = _FakeKeyboard()
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

    def get_by_test_id(self, test_id):
        assert test_id == "backdrop"
        return self.backdrop


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


def test_fetch_all_reviews_stops_before_any_click_when_initial_load_is_all_known():
    # 초기 자동 로드 안에서 신규 1건 + 이미 아는 5건이 한 번에 온 경우,
    # "더보기"를 단 한 번도 클릭하지 않고 끝나야 한다 — 이미 5연속 known을
    # 확인했으므로 그 이상 조회할 필요가 없다고 판단한다.
    known_ids = {100, 101, 102, 103, 104}

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not getattr(self, "_fired", False):
                self._fired = True
                reviews = [{**_RAW_REVIEW, "id": 999}] + [
                    {**_RAW_REVIEW, "id": i} for i in (100, 101, 102, 103, 104)
                ]
                self._handlers["response"](_review_response(reviews))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 6
    assert p.more_button.click_calls == 0


def test_fetch_all_reviews_keeps_paginating_when_known_run_is_interrupted():
    # known id들이 연속되지 않고 중간에 신규 리뷰가 끼어 있으면(전체적으로는
    # known이 5개 있어도 "연속"은 아님) 정상적으로 계속 페이지네이션해야
    # 한다 — 기존 "더보기" 종료 조건(연속 2회 무진행)만 적용된다.
    known_ids = {100, 101, 102, 103, 104}

    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                reviews = [
                    {**_RAW_REVIEW, "id": 100}, {**_RAW_REVIEW, "id": 101},
                    {**_RAW_REVIEW, "id": 999},  # 연속을 끊는 신규 리뷰
                    {**_RAW_REVIEW, "id": 102}, {**_RAW_REVIEW, "id": 103},
                ]
                self._handlers["response"](_review_response(reviews))
            # 이후 클릭에서는 응답 없음 — 무진행 카운터로 정상 종료.

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 5
    assert p.more_button.click_calls == 2  # 연속 2회 무진행으로 종료(기존 규칙)


def test_fetch_all_reviews_stops_mid_pagination_once_five_consecutive_known_seen():
    # 초기 로드는 전부 신규라 계속 진행하다가, 첫 "더보기" 클릭에서 받은
    # 응답이 이미 아는 리뷰 5개 연속이면 그 시점에서 멈춰야 한다 — 두 번째
    # 클릭은 일어나면 안 된다.
    known_ids = {200, 201, 202, 203, 204}

    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([{**_RAW_REVIEW, "id": 999}]))
            elif self._wait_count == 2:
                reviews = [{**_RAW_REVIEW, "id": i} for i in (200, 201, 202, 203, 204)]
                self._handlers["response"](_review_response(reviews))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 6
    assert p.more_button.click_calls == 1  # 초기 로드 + 1번 더보기 클릭 후 조기 종료


def test_fetch_all_reviews_with_no_existing_ids_behaves_exactly_as_before():
    # existing_ids를 안 넘기면(기본값 None) 기존 동작(연속 2회 무진행까지
    # 계속 페이지네이션)과 완전히 동일해야 한다 — 최초 동기화 경로 회귀 방지.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert p.more_button.click_calls == 2


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


def test_fetch_all_reviews_retries_after_click_timeout_instead_of_giving_up_immediately():
    # 클릭 자체가 PlaywrightTimeoutError로 실패해도(예: backdrop이 클릭 순간에
    # 막 떠서 요소가 안정되지 않은 경우) 곧바로 포기하지 않는다 — 실 계정
    # 재현에서 첫 클릭이 타임아웃 났는데도 그 사이에 실제로는 데이터가 계속
    # 로드되고 있었던 사례가 있었다(모듈 docstring 참고). 그래서 매번 클릭이
    # 타임아웃 나더라도 무진행 카운터가 실제로 종료 조건을 채울 때까지는
    # 계속 재시도해야 한다 — 정확히 2회(연속 무진행 한도) 시도 후 멈춘다.
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
    assert p.more_button.click_calls == 2


def test_fetch_all_reviews_dismisses_backdrop_that_reappears_mid_load_and_keeps_progressing():
    # 실 계정 재현(398건 매장): backdrop이 로그인 직후뿐 아니라 리뷰 목록을
    # 계속 불러오는 도중에도 다시 뜰 수 있고, 그걸 방치하면 "더보기" 클릭이
    # 막혀 무진행으로 오판해 조기 종료한다(실제로 이 버그 때문에 140건
    # 근처에서 멈췄었다). backdrop이 뜬 것을 감지하면 매 클릭 시도 전에
    # Escape로 닫아야 계속 진행할 수 있다는 게 이 테스트의 핵심 — backdrop이
    # 계속 present인 상태를 흉내내면서, 실제 코드가 호출하는 대기 시간(ms
    # 인자)으로 "초기 로드 대기"와 "클릭 후 대기"를 구분해 각 단계에서 새
    # 리뷰가 도착하는 상황을 흉내낸다. backdrop을 닫기 위한 500ms 대기에는
    # 아무 응답도 쏘지 않는다(실제로도 그 대기는 리뷰 로드와 무관하다).
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self.backdrop.present = True  # 처음부터, 그리고 계속 backdrop이 떠 있는 상태

        def wait_for_timeout(self, ms):
            if ms == _INITIAL_LOAD_WAIT_MS:
                self._handlers["response"](_review_response([{**_RAW_REVIEW, "id": 9_000_000}]))
            elif ms == _LOAD_MORE_WAIT_MS:
                self._handlers["response"](
                    _review_response([{**_RAW_REVIEW, "id": 9_000_000 + self.more_button.click_calls}])
                )
            # ms가 그 외 값(backdrop을 닫은 뒤의 500ms)이면 아무 응답도 쏘지 않는다.

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    # backdrop이 계속 떠 있었지만(매번 present=True로 유지) 매 클릭 전에
    # Escape로 닫아줬기 때문에 무진행 없이 하드 캡까지 전부 진행됐다.
    assert p.more_button.click_calls == 30
    assert len(result) == 31  # 초기 로드 1건 + 클릭 30회, 각 클릭마다 새 리뷰 1건
    assert p.keyboard.press_calls.count("Escape") == 30
