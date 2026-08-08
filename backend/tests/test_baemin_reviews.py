from datetime import datetime
from urllib.parse import parse_qs, urlparse

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


def _offset_of(url: str) -> int:
    return int(parse_qs(urlparse(url).query)["offset"][0])


def _limit_of(url: str) -> int:
    return int(parse_qs(urlparse(url).query)["limit"][0])


class _FakePage:
    """page.evaluate(script, url) 흉내. 실제 코드는 page.evaluate()로 인증된
    페이지 안에서 fetch()를 실행해 {status, body} 딕셔너리를 돌려받는다 —
    브라우저 밖 request_context.get()이 아니다(x-e-request 서명 헤더 때문에
    403이 나서 바뀐 구조)."""

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self.calls: list[str] = []

    def evaluate(self, script, url):
        self.calls.append(url)
        page = self._pages[_offset_of(url) // _limit_of(url)]
        return {"status": 200, "body": page}


def test_fetch_all_reviews_paginates_until_next_is_false():
    pages = [
        {"reviews": [_RAW_REVIEW], "next": True},
        {"reviews": [{**_RAW_REVIEW, "id": 999}], "next": False},
    ]
    page = _FakePage(pages)

    result = fetch_all_reviews(page, shop_no=14804318, limit=1)

    assert len(result) == 2
    assert [r["id"] for r in result] == [2026080402827696, 999]
    assert len(page.calls) == 2
    assert _offset_of(page.calls[0]) == 0
    assert _offset_of(page.calls[1]) == 1


def test_fetch_all_reviews_raises_on_non_200():
    class _FailingPage:
        def evaluate(self, script, url):
            return {"status": 401, "body": {}}

    with pytest.raises(BaeminScrapeError):
        fetch_all_reviews(_FailingPage(), shop_no=1)
