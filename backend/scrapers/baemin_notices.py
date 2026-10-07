"""배민 사장님광장의 "사장님공지" 화면에서 공지 목록을 가져온다. 리뷰관리와
같은 API 네임스페이스(/v1/review/shops/...)지만 좌측 메뉴의 별도
"사장님공지" 항목을 클릭해야 발생하는 organic 응답이다(실측 확인,
2026-10 — 설계 문서 2026-10-03-ai-agent-llmops-reply-design.md 1.2절
참고). baemin_menu.py와 달리 page.goto() 직접 진입이 아니라 사이드바
클릭이 필요하다는 게 다르다 — "사장님공지"는 메뉴관리 URL 체계 밖의
별도 화면이라 shop_no만으로 URL을 구성할 수 없다(실측 확인된 URL
패턴이 없음)."""

import re
from datetime import datetime, timezone
from urllib.parse import urlparse


class BaeminNoticesScrapeError(Exception):
    pass


def _dismiss_backdrop_if_present(page) -> None:
    # baemin_stats.py/baemin_ads.py와 동일한 패턴(2026-10-08) — 프로모션
    # 팝업이 사이드바 클릭을 가로챈 게 사용자 실측으로 재현됐다. "N일간
    # 보지 않기"류 옵션을 먼저 찾아 눌러 같은 세션 동안 다시 안 뜨게
    # 만들고, 없으면 Escape로 넘어간다.
    if page.get_by_test_id("backdrop").count() == 0:
        return
    dont_show_again = page.get_by_text(
        re.compile(r"(\d+일|오늘\s*하루)\s*(간)?\s*(다시\s*)?(보지|열지)\s*않기")
    ).first
    if dont_show_again.count() > 0:
        try:
            dont_show_again.click(timeout=2_000)
            page.wait_for_timeout(500)
            return
        except Exception:
            pass
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)


def map_notices(raw: dict) -> list[dict]:
    """ceo/notices 응답을 우리 스키마로 변환한다. createdAt은 타임존 없는
    ISO 문자열(예: "2025-10-17T13:37:00.14668")이라 UTC로 간주해 attach한다
    — 배민 API가 돌려주는 시각이 실측상 UTC였다(사장님공지는 리뷰처럼
    한국 벽시계 시간이 아니라 이미 UTC 포맷으로 옴, baemin_reviews.py의
    parse_baemin_datetime과는 다른 포맷이라 재사용하지 않는다)."""
    result = []
    for notice in raw.get("notices", []):
        created_raw = notice.get("createdAt")
        created_at = datetime.fromisoformat(created_raw).replace(tzinfo=timezone.utc) if created_raw else None
        result.append({
            "external_notice_id": notice["id"],
            "contents": notice.get("contents") or "",
            "display_status": notice.get("displayStatus", "DISPLAY"),
            "block_type": notice.get("blockType", "NONE"),
            "notice_created_at": created_at,
        })
    return result


def fetch_ceo_notices(page, shop_no: int) -> list[dict]:
    """로그인된 page로 사이드바의 "사장님공지" 메뉴를 클릭해 공지 목록을
    가져온다. 응답을 못 받으면(레이아웃 변경, 공지가 아예 없는 매장 등)
    빈 리스트를 반환한다 — baemin_menu.py와 달리 "공지 없음"이 정상
    상태일 수 있어(신규 매장은 공지를 안 쓸 수 있음) 하드 에러로 막지
    않는다."""
    captured: dict | None = None

    def _on_response(response) -> None:
        nonlocal captured
        if response.status != 200:
            return
        path = urlparse(response.url).path
        if path == f"/v1/review/shops/{shop_no}/ceo/notices":
            try:
                captured = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        nav_item = page.get_by_text("사장님공지", exact=True)
        try:
            nav_item.click(timeout=5_000)
        except Exception:
            # 낯선 프로모션 팝업이 가로막았을 수 있다 — 치우고 한 번 더
            # 시도한다(2026-10-08, 사용자 실측 재현).
            _dismiss_backdrop_if_present(page)
            try:
                nav_item.click(timeout=5_000)
            except Exception as e:
                raise BaeminNoticesScrapeError(f"사장님공지 메뉴 진입에 실패했습니다: {e}") from e
        page.wait_for_timeout(3_000)
    finally:
        page.remove_listener("response", _on_response)

    if captured is None:
        return []
    return map_notices(captured)
