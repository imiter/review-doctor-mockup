"""배민 리뷰 API 응답(HTML 파싱이 아니라 실제 JSON)에서 리뷰를 가져와 우리
스키마 필드로 매핑한다.

### 이전 접근이 틀렸던 이유 (실 계정 브라우저 콘솔로 직접 확인)

이전에는 인증된 세션의 살아있는 Playwright `page` 안에서 `page.evaluate()`로
직접 `fetch()`를 실행했다 — "페이지 안에서 실행하면 사이트의 요청 서명 로직이
자동으로 적용될 것"이라는 가정이었다. `page.on("console")`/`page.on("response")`
리스너로 실 계정 로그인 세션을 직접 캡처해보니 이 가정은 틀렸다. 정확히 이
에러가 났다:

    Access to fetch at 'https://self-api.baemin.com/v1/review/shops/14804912/
    reviews?from=2026-08-01&to=2026-08-10&offset=0&limit=1' from origin
    'https://self.baemin.com' has been blocked by CORS policy: No
    'Access-Control-Allow-Origin' header is present on the requested resource.

즉 배민 사이트는 전역 `window.fetch`를 패치하지 않는다. 번들/축소된 JS 안에
별도의 내부 API 클라이언트가 있어서, 실제 네트워크 계층을 호출하기 전에
`x-e-request` 서명 헤더를 계산해 붙인다. 우리가 페이지 컨텍스트 안에서라도
가공되지 않은 raw `fetch()`를 직접 실행하면 이 서명 로직을 완전히 건너뛰게
되고, 서버는 CORS 승인 헤더 없이 응답한다 — 브라우저는 이를 "차단된 요청"으로
보고한다(위 에러).

### 현재 접근: 우리가 요청을 만들지 않고, 페이지가 스스로 발생시키는 응답을 가로챈다

같은 진단에서 반대로 확인된 사실: 페이지 자신이 "리뷰관리" 화면을 로드할 때는
스스로 올바르게 서명된 요청을 같은 API 엔드포인트에 보내고, 정상적으로 200
OK와 실제 리뷰 JSON을 받는다 — 아무 상호작용 없이 화면에 진입하기만 해도:

    [captured review response] https://self-api.baemin.com/v1/review/shops/
    14804912/reviews?from=2026-02-11&to=2026-08-10&offset=0&limit=10 status: 200
    [captured review response] https://self-api.baemin.com/v1/review/shops/
    14804912/reviews?from=2026-02-11&to=2026-08-10&offset=10&limit=10 status: 200

그래서 이 모듈은 우리가 요청 URL/파라미터를 구성해서 보내는 대신,
`page.on("response", ...)`로 페이지가 organically 발생시키는 네트워크 응답을
가로채 리뷰 리스트 엔드포인트(`/v1/review/shops/{shop_no}/reviews?from=...`,
`/reviews/stat`이나 `/reviews/count`, `/sort-type`, `/analysis/available` 같은
동일 화면의 다른 API 호출은 제외)만 골라 파싱하고, `id` 기준으로 중복 제거해
누적한다.

### 알려진 한계 (지금 단계에서는 더 해결하지 않는, 받아들이는 한계)

리뷰관리 화면에 진입하면 최근 리뷰 약 20건(오프셋 0/10, 각 limit=10인 두
페이지, 최근 약 6개월치)이 상호작용 없이 자동으로 로드된다(실 계정 진단으로
확인). 추가 페이지네이션은 마우스 휠 스크롤로 best-effort 시도하지만, 실
계정으로 5회 스크롤을 테스트했을 때 처음 자동 로드된 2페이지를 넘어서는 추가
요청이 전혀 발생하지 않았다(실제 스크롤 가능한 리스트 컨테이너를 스크롤 휠이
타겟하지 못했거나, 이 정도 상호작용으로는 페이지가 추가로 lazy-load 하지 않는
것으로 추정). 즉 한 번의 동기화로 매장의 전체 리뷰 이력을 다 가져온다는 보장은
없다 — 이번 단계에서는 받아들이는 한계로 남겨두고 더 해결하지 않는다.

인증 자체는 `baemin_auth.login()`이 반환한 세션이 담당한다.
"""

from datetime import datetime

_MAX_SCROLL_ATTEMPTS = 5
_SCROLL_WAIT_MS = 1_500
_INITIAL_LOAD_WAIT_MS = 3_000


class BaeminScrapeError(Exception):
    pass


def _review_list_prefix(shop_no: int) -> str:
    return f"/v1/review/shops/{shop_no}/reviews?from="


def fetch_all_reviews(page, shop_no: int) -> list[dict]:
    """`page`가 리뷰관리 화면을 로드하며 organically 발생시키는 리뷰 리스트
    응답을 가로채 수집한다. 우리는 요청을 직접 만들지 않는다 (모듈 docstring
    참고 — raw fetch()는 CORS로 차단된다).
    """
    prefix = _review_list_prefix(shop_no)
    collected: dict[int, dict] = {}

    def _on_response(response) -> None:
        url = response.url
        if prefix not in url:
            return
        if response.status != 200:
            return
        try:
            body = response.json()
        except Exception:
            return
        for raw in body.get("reviews", []):
            collected[raw["id"]] = raw

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/reviews")
        except Exception as e:
            raise BaeminScrapeError(f"리뷰 페이지 이동에 실패했습니다: {e}") from e

        # 리뷰관리 화면은 진입 즉시(상호작용 없이) 최근 리뷰 2페이지를
        # organic하게 로드한다 — 완료를 알리는 신호가 따로 없어 유한 시간만큼
        # 기다린다.
        page.wait_for_timeout(_INITIAL_LOAD_WAIT_MS)

        # 추가 페이지네이션은 best-effort. 연속 2번의 스크롤이 새 리뷰를
        # 하나도 못 얻으면 더 스크롤해도 소용없다고 보고 조기 종료한다.
        consecutive_empty_scrolls = 0
        for _ in range(_MAX_SCROLL_ATTEMPTS):
            before = len(collected)
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(_SCROLL_WAIT_MS)
            if len(collected) > before:
                consecutive_empty_scrolls = 0
            else:
                consecutive_empty_scrolls += 1
                if consecutive_empty_scrolls >= 2:
                    break
    finally:
        page.remove_listener("response", _on_response)

    # 리뷰 0건은 매장에 리뷰가 아직 없다는 뜻일 수도 있는 정상 데이터다 —
    # 여기서는 에러로 취급하지 않는다. BaeminScrapeError는 위에서 이미 페이지
    # 이동 실패 같은 명확한 실패 신호에만 raise한다.
    return list(collected.values())


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
