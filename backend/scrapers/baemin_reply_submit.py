"""배민 사장님광장 리뷰관리 화면에서 리뷰에 실제로 답글을 등록한다.

### 실 계정 실측(2026-08-25, 치밥대장, 5점 리뷰 1건)

리뷰관리 화면(`/shops/{shopNo}/reviews`)의 각 리뷰 카드에는 "사장님 댓글
등록하기" 버튼이 있다. 클릭하면 인라인 `<textarea>`가 열리는데, 배민이
자동으로 "{닉네임}님, " 문구를 미리 채워둔다 — 우리 AI 생성 답글은 이미
자체적으로 인사말을 포함하므로("안녕하세요, {가게명}입니다..."), 이
프리필을 그대로 두고 이어붙이면 인사말이 중복된다. 그래서 `fill()`로
prefill을 완전히 덮어쓴다(Playwright의 `fill()`은 append가 아니라
교체다).

textarea 아래 "취소"/"등록" 버튼 중 "등록"을 클릭하면 페이지 자신이
organic하게 서명된 요청을 보낸다(다른 배민 스크래핑과 동일한 이유로 —
`baemin_reviews.py` 모듈 docstring 참고 — 우리가 직접 fetch()를 구성하면
`x-e-request` 서명이 없어 차단된다):

    POST https://self-api.baemin.com/v1/review/shops/{shopNo}/reviews/comments
    {"contents": "...", "reviewId": <external_review_id>, "shopNo": <shopNo>}

응답 200이면 실제로 배민에 반영된다(같은 세션으로 화면 확인 결과, 리뷰
카드에 "사장님" 댓글이 등록되고 "삭제"/"수정" 버튼이 생김 — 실측
완료). 이 함수는 리뷰 목록에서 "리뷰번호 {external_review_id}" 텍스트로
대상 리뷰 카드를 찾는다(리뷰 내용으로 찾으면 서로 다른 고객이 비슷한
텍스트를 남겼을 때 오답글이 달릴 위험이 있어 안 씀). 목록 로딩 직후
안 보이면 `baemin_reviews.py`의 "더보기" 패턴과 동일하게 버튼을 반복
클릭하며 찾는다.
"""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_MAX_LOAD_MORE_CLICKS = 30
_LOAD_MORE_WAIT_MS = 1_500
_INITIAL_LOAD_WAIT_MS = 3_000


class BaeminReplySubmitError(Exception):
    pass


def _dismiss_promo_modal(page) -> None:
    # 로그인 세션 안에서 리뷰 목록을 다시 열 때마다 "재주문금액 할인" 같은
    # 프로모션 모달이 뜰 수 있다(2026-08-25 실측 확인, baemin_reviews.py가
    # 이미 다루는 "더보기" backdrop과 같은 종류의 방해 요소). "오늘 하루
    # 보지 않기"를 눌러 이번 세션 동안 다시 뜨지 않게 한다.
    dismiss = page.get_by_text("오늘 하루 보지 않기", exact=False)
    if dismiss.count() > 0:
        dismiss.first.click()
        page.wait_for_timeout(500)


def _find_reply_button_for_review(page, external_review_id: int):
    """`external_review_id`에 해당하는 리뷰 카드의 "사장님 댓글 등록하기"
    버튼을 찾는다. 리뷰 카드 DOM에 안정적인 test-id가 없어서, "리뷰번호
    {id}" 텍스트의 y좌표와 가장 가까운(그 아래에 있는) 답글 버튼을
    고른다 — 각 리뷰 카드 안에서 리뷰번호가 항상 답글 버튼보다 위에
    있다(실측 확인)."""
    marker = page.get_by_text(f"리뷰번호 {external_review_id}", exact=False)
    if marker.count() == 0:
        return None
    marker_box = marker.first.bounding_box()
    if marker_box is None:
        return None

    reply_buttons = page.get_by_role("button", name="사장님 댓글 등록하기")
    best_idx, best_dy = None, None
    for i in range(reply_buttons.count()):
        box = reply_buttons.nth(i).bounding_box()
        if box is None:
            continue
        dy = box["y"] - marker_box["y"]
        if dy >= -50 and (best_dy is None or dy < best_dy):
            best_dy, best_idx = dy, i
    if best_idx is None:
        return None
    return reply_buttons.nth(best_idx)


def submit_reply(page, shop_no: int, external_review_id: int, content: str) -> None:
    """로그인된 `page`로 특정 리뷰에 답글을 실제로 등록한다. 성공하면
    조용히 반환하고, 실패하면 BaeminReplySubmitError를 던진다."""
    try:
        page.goto(f"https://self.baemin.com/shops/{shop_no}/reviews")
    except Exception as e:
        raise BaeminReplySubmitError(f"리뷰 페이지 이동에 실패했습니다: {e}") from e

    page.wait_for_timeout(_INITIAL_LOAD_WAIT_MS)
    _dismiss_promo_modal(page)

    reply_button = None
    for _ in range(_MAX_LOAD_MORE_CLICKS):
        reply_button = _find_reply_button_for_review(page, external_review_id)
        if reply_button is not None:
            break
        more_button = page.get_by_text("더보기", exact=True)
        if more_button.count() == 0:
            break
        if page.get_by_test_id("backdrop").count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        try:
            more_button.first.scroll_into_view_if_needed()
            more_button.first.click(timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(_LOAD_MORE_WAIT_MS)

    if reply_button is None:
        raise BaeminReplySubmitError(
            f"리뷰(external_review_id={external_review_id})를 목록에서 찾지 못했습니다"
        )

    try:
        reply_button.click(timeout=10_000)
    except PlaywrightTimeoutError as e:
        raise BaeminReplySubmitError(f"답글 입력창을 여는 데 실패했습니다: {e}") from e

    page.wait_for_timeout(500)
    textarea = page.locator("textarea").first
    try:
        # fill()은 기존 값을 완전히 교체한다 — 배민이 자동으로 채워둔
        # "{닉네임}님, " 프리필을 우리 답글 전문으로 덮어쓴다(모듈 docstring 참고).
        textarea.fill(content)
    except PlaywrightTimeoutError as e:
        raise BaeminReplySubmitError(f"답글 입력에 실패했습니다: {e}") from e

    captured = {"status": None}

    def _on_response(response) -> None:
        if response.request.method == "POST" and response.url.endswith("/reviews/comments"):
            captured["status"] = response.status

    page.on("response", _on_response)
    try:
        submit_button = page.get_by_role("button", name="등록", exact=True)
        submit_button.first.click(timeout=10_000)
        page.wait_for_timeout(3_000)
    except PlaywrightTimeoutError as e:
        raise BaeminReplySubmitError(f"답글 등록 버튼 클릭에 실패했습니다: {e}") from e
    finally:
        page.remove_listener("response", _on_response)

    if captured["status"] != 200:
        raise BaeminReplySubmitError(
            f"답글 등록 요청이 실패했습니다(status={captured['status']})"
        )
