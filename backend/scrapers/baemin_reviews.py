"""배민 리뷰 API(HTML 파싱이 아니라 직접 HTTP 호출)에서 리뷰를 가져오고 우리
스키마 필드로 매핑한다. 인증은 baemin_auth.login()이 반환한 세션이 담당한다.
"""

from datetime import datetime, timedelta, timezone

_REVIEWS_URL_TEMPLATE = "https://self-api.baemin.com/v1/review/shops/{shop_no}/reviews"


class BaeminScrapeError(Exception):
    pass


def fetch_all_reviews(
    request_context, shop_no: int,
    date_from: str | None = None, date_to: str | None = None, limit: int = 20,
) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    date_from = date_from or (today - timedelta(days=730)).isoformat()
    date_to = date_to or today.isoformat()

    reviews: list[dict] = []
    offset = 0
    while True:
        resp = request_context.get(
            _REVIEWS_URL_TEMPLATE.format(shop_no=shop_no),
            params={"from": date_from, "to": date_to, "offset": offset, "limit": limit},
        )
        if resp.status != 200:
            raise BaeminScrapeError(f"리뷰 조회 실패: HTTP {resp.status}")
        body = resp.json()
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
