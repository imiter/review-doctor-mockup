"""배민 리뷰 API(HTML 파싱이 아니라 직접 HTTP 호출)에서 리뷰를 가져오고 우리
스키마 필드로 매핑한다. 인증된 세션의 살아있는 Playwright `page` 안에서
`page.evaluate()`로 `fetch()`를 실행한다 — 브라우저 밖 `APIRequestContext`로
직접 호출하면 매 요청마다 값이 바뀌는 `x-e-request` 서명 헤더가 없어 HTTP
403이 나기 때문이다(baemin_auth.py 모듈 docstring 참고). 인증 자체는
baemin_auth.login()이 반환한 세션이 담당한다.
"""

from datetime import datetime, timedelta, timezone

_REVIEWS_URL_TEMPLATE = "https://self-api.baemin.com/v1/review/shops/{shop_no}/reviews"


class BaeminScrapeError(Exception):
    pass


def fetch_all_reviews(
    page, shop_no: int,
    date_from: str | None = None, date_to: str | None = None, limit: int = 20,
) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    date_from = date_from or (today - timedelta(days=730)).isoformat()
    date_to = date_to or today.isoformat()

    reviews: list[dict] = []
    offset = 0
    while True:
        url = (
            f"{_REVIEWS_URL_TEMPLATE.format(shop_no=shop_no)}"
            f"?from={date_from}&to={date_to}&offset={offset}&limit={limit}"
        )
        result = page.evaluate(
            """async (requestUrl) => {
                const res = await fetch(requestUrl, { credentials: 'include' });
                const body = await res.json();
                return { status: res.status, body };
            }""",
            url,
        )
        if result["status"] != 200:
            raise BaeminScrapeError(f"리뷰 조회 실패: HTTP {result['status']}")
        body = result["body"]
        reviews.extend(body["reviews"])
        if not body.get("next"):
            break
        offset += limit
    return reviews


def map_review(raw: dict, store_id: int, platform_id: int) -> dict:
    menus = raw.get("menus") or []
    if not menus:
        menu_summary = "메뉴 정보 없음"
    elif len(menus) == 1:
        menu_summary = menus[0]["name"]
    else:
        menu_summary = f"{menus[0]['name']} 외 {len(menus) - 1}건"

    return {
        "external_review_id": raw["id"],
        "rating": round(raw["rating"]),
        "content": raw.get("contents") or "",
        "customer_nickname": raw["memberNickname"],
        "customer_order_count": raw.get("orderCount", 1),
        "menu_summary": menu_summary,
        "created_at": datetime.fromisoformat(raw["createdAt"]),
        "store_id": store_id,
        "platform_id": platform_id,
        "status": "unanswered",
    }
