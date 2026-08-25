import pytest

from scrapers.baemin_reply_submit import (
    BaeminReplySubmitError,
    _dismiss_promo_modal,
    _find_reply_button_for_review,
    submit_reply,
)


class _FakeButtonLocator:
    """단일 버튼(하나만 매칭되는 경우)의 최소 흉내: count/first/click."""

    def __init__(self, present=True, click_raises=None):
        self.present = present
        self.click_raises = click_raises
        self.click_calls = 0

    def count(self):
        return 1 if self.present else 0

    @property
    def first(self):
        return self

    def click(self, timeout=None, exact=None):
        self.click_calls += 1
        if self.click_raises:
            raise self.click_raises


class _FakeReplyButton:
    def __init__(self, box):
        self._box = box
        self.clicked = False

    def bounding_box(self):
        return self._box

    def click(self, timeout=None):
        self.clicked = True


class _FakeReplyButtonCollection:
    """`get_by_role("button", name="사장님 댓글 등록하기")`의 흉내. 여러 개(count)
    있을 수 있고, 각각 다른 y좌표를 가진다 — _find_reply_button_for_review가
    marker보다 아래에 있는 것 중 가장 가까운 걸 골라야 한다."""

    def __init__(self, boxes):
        self._buttons = [_FakeReplyButton(b) for b in boxes]

    def count(self):
        return len(self._buttons)

    def nth(self, i):
        return self._buttons[i]


class _FakeMarkerLocator:
    def __init__(self, present, box=None):
        self.present = present
        self._box = box

    def count(self):
        return 1 if self.present else 0

    @property
    def first(self):
        return self

    def bounding_box(self):
        return self._box


class _FakeTextarea:
    def __init__(self):
        self.filled_value = None

    def fill(self, value, timeout=None):
        self.filled_value = value


class _FakeTextareaLocator:
    def __init__(self):
        self.textarea = _FakeTextarea()

    @property
    def first(self):
        return self.textarea


class _FakePage:
    """submit_reply가 실제로 호출하는 최소 인터페이스만 흉내낸다."""

    def __init__(self, *, marker_present, marker_box, reply_button_boxes, submit_status=200,
                 more_button_present=False):
        self.goto_calls = []
        self._marker_present = marker_present
        self._marker_box = marker_box
        self._reply_button_boxes = reply_button_boxes
        self._submit_status = submit_status
        self._more_button = _FakeButtonLocator(present=more_button_present)
        self._backdrop = _FakeButtonLocator(present=False)
        self.textarea_locator = _FakeTextareaLocator()
        self.reply_buttons = _FakeReplyButtonCollection(self._reply_button_boxes)
        self._handlers = {}
        self.dismiss_clicked = False
        self.submit_clicked = False

    def goto(self, url):
        self.goto_calls.append(url)

    def wait_for_timeout(self, ms):
        pass

    def get_by_text(self, text, exact=False):
        if "리뷰번호" in text:
            return _FakeMarkerLocator(self._marker_present, self._marker_box)
        if text == "오늘 하루 보지 않기":
            loc = _FakeButtonLocator(present=False)
            return loc
        if text == "더보기":
            return self._more_button
        raise AssertionError(f"unexpected get_by_text: {text}")

    def get_by_test_id(self, test_id):
        return self._backdrop

    def get_by_role(self, role, name=None, exact=None):
        if name == "사장님 댓글 등록하기":
            return self.reply_buttons
        if name == "등록":
            btn = _FakeButtonLocator(present=True)
            original_click = btn.click

            def _click(timeout=None):
                self.submit_clicked = True
                original_click(timeout=timeout)
                handler = self._handlers.get("response")
                if handler:
                    handler(_FakeCommentResponse(self._submit_status))

            btn.click = _click
            return btn
        raise AssertionError(f"unexpected get_by_role: {name}")

    def locator(self, selector):
        assert selector == "textarea"
        return self.textarea_locator

    def on(self, event, handler):
        self._handlers[event] = handler

    def remove_listener(self, event, handler):
        self._handlers.pop(event, None)


class _FakeCommentResponse:
    def __init__(self, status):
        self.status = status

        class _Req:
            method = "POST"
        self.request = _Req()
        self.url = "https://self-api.baemin.com/v1/review/shops/14804318/reviews/comments"


def test_find_reply_button_picks_closest_button_below_marker():
    page = _FakePage(
        marker_present=True, marker_box={"y": 500},
        reply_button_boxes=[{"y": 100}, {"y": 650}, {"y": 900}],
    )
    button = _find_reply_button_for_review(page, 123)
    assert button is page.reply_buttons._buttons[1]


def test_find_reply_button_returns_none_when_marker_missing():
    page = _FakePage(marker_present=False, marker_box=None, reply_button_boxes=[{"y": 100}])
    assert _find_reply_button_for_review(page, 123) is None


def test_submit_reply_fills_textarea_and_succeeds_on_200():
    page = _FakePage(
        marker_present=True, marker_box={"y": 500},
        reply_button_boxes=[{"y": 650}],
        submit_status=200,
    )

    submit_reply(page, shop_no=14804318, external_review_id=2026082401542683, content="감사합니다!")

    assert page.textarea_locator.textarea.filled_value == "감사합니다!"
    assert page.submit_clicked is True


def test_submit_reply_raises_when_review_never_found():
    page = _FakePage(
        marker_present=False, marker_box=None, reply_button_boxes=[],
        more_button_present=False,
    )
    with pytest.raises(BaeminReplySubmitError, match="찾지 못했습니다"):
        submit_reply(page, shop_no=14804318, external_review_id=999, content="감사합니다!")


def test_submit_reply_raises_when_submission_response_is_not_200():
    page = _FakePage(
        marker_present=True, marker_box={"y": 500},
        reply_button_boxes=[{"y": 650}],
        submit_status=500,
    )
    with pytest.raises(BaeminReplySubmitError, match="실패했습니다"):
        submit_reply(page, shop_no=14804318, external_review_id=2026082401542683, content="감사합니다!")


def test_dismiss_promo_modal_clicks_when_present():
    class _Page:
        def __init__(self):
            self.btn = _FakeButtonLocator(present=True)

        def get_by_text(self, text, exact=False):
            assert text == "오늘 하루 보지 않기"
            return self.btn

        def wait_for_timeout(self, ms):
            pass

    page = _Page()
    _dismiss_promo_modal(page)
    assert page.btn.click_calls == 1


def test_dismiss_promo_modal_noop_when_absent():
    class _Page:
        def __init__(self):
            self.btn = _FakeButtonLocator(present=False)

        def get_by_text(self, text, exact=False):
            return self.btn

        def wait_for_timeout(self, ms):
            pass

    page = _Page()
    _dismiss_promo_modal(page)  # should not raise
    assert page.btn.click_calls == 0
