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

### 페이지네이션: 스크롤이 아니라 명시적 "더보기" 버튼 (재조사로 갱신)

리뷰관리 화면에 진입하면 최근 리뷰 약 20건(오프셋 0/10, 각 limit=10인 두
페이지, 최근 약 6개월치)이 상호작용 없이 자동으로 로드된다(실 계정 진단으로
확인). 처음에는 추가 페이지네이션이 마우스 휠 스크롤로 트리거될 것으로
가정했으나, 실 계정으로 5회 스크롤을 테스트했을 때 처음 자동 로드된 2페이지를
넘어서는 추가 요청이 전혀 발생하지 않아 "스크롤은 신뢰할 수 없다"로 결론
내렸었다.

이후 실 계정으로 다시 조사한 결과, 이 화면은 애초에 스크롤 기반 무한 로드가
아니라 리스트 하단에 명시적 "더보기" 버튼이 있다는 사실이 확인됐다(고정
`data-testid`/`aria-label`은 없고 텍스트로만 식별 가능 — 이 코드베이스가 이미
쓰는 "홈으로 이동"/"리뷰관리 리뷰관리"/"로그인" 버튼 선택자와 같은 부류). 이
버튼을 반복 클릭하면 매번 organic하게 올바르게 서명된 추가 요청이 발생한다.

실 계정 재현: 20건(초기 자동 로드) → 40건(1차 클릭) → 52건(2차 클릭) → 이후
반복 클릭에서는 더 이상 새 리뷰가 늘지 않음(해당 매장의 조회 기간(~6개월)
전체 리뷰 수가 52건이었던 것으로 확인). 즉 이 방식으로는 화면이 기본으로
잡는 기간 안에서 매장의 전체 리뷰 이력을 안정적으로 다 가져올 수 있다 — 이건
지금 확인된 실측 동작이지, 배민이 이 버튼의 마크업이나 동작을 바꿔도 계속
유지된다는 보장은 아니다.

실 계정 관찰상 "더보기" 버튼은 리스트가 끝에 도달해도 사라지거나
비활성화되지 않고 계속 클릭 가능한 상태로 남아있었다 — 그래서 버튼 존재
여부(`count() == 0`)만으로는 종료 시점을 판단할 수 없고, 연속 클릭 무진행
카운터(`_MAX_CONSECUTIVE_NO_PROGRESS`)가 실질적인 종료 신호다. 클릭
횟수에는 별도로 하드 캡(`_MAX_LOAD_MORE_CLICKS`)을 둬서, 위 가정이 어떤
매장/상황에서 깨지더라도(예: 무진행 카운터가 어떤 이유로 절대 트리거되지
않는 경우) 무한 루프에 빠지지 않게 방어한다.

인증 자체는 `baemin_auth.login()`이 반환한 세션이 담당한다.
"""

from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_MAX_LOAD_MORE_CLICKS = 30
_MAX_CONSECUTIVE_NO_PROGRESS = 2
_LOAD_MORE_WAIT_MS = 1_500
_INITIAL_LOAD_WAIT_MS = 3_000


class BaeminScrapeError(Exception):
    pass


def _review_list_path(shop_no: int) -> str:
    return f"/v1/review/shops/{shop_no}/reviews"


def fetch_all_reviews(page, shop_no: int) -> list[dict]:
    """`page`가 리뷰관리 화면을 로드하며 organically 발생시키는 리뷰 리스트
    응답을 가로채 수집한다. 우리는 요청을 직접 만들지 않는다 (모듈 docstring
    참고 — raw fetch()는 CORS로 차단된다).
    """
    path = _review_list_path(shop_no)
    collected: dict[int, dict] = {}
    state = {"observed_review_endpoint": False}

    def _on_response(response) -> None:
        url = response.url
        if urlparse(url).path != path:
            return
        # 상태 코드와 무관하게 "리뷰 목록 엔드포인트 자체는 응답을 줬다"는
        # 사실은 기록한다 — 401/500이 와도 우리가 올바른 엔드포인트를
        # 찾긴 했다는 뜻이므로, 아래 200 파싱 실패와는 구분해야 한다.
        state["observed_review_endpoint"] = True
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

        # 추가 페이지네이션은 리스트 하단의 "더보기" 버튼을 반복 클릭해
        # 트리거한다(모듈 docstring 참고 — 스크롤이 아니라 명시적 버튼).
        # 버튼은 리스트가 끝에 도달해도 사라지지 않는 것으로 관찰돼
        # count() == 0은 보너스 조기 종료일 뿐, 연속 무진행 카운터가 실질적인
        # 종료 조건이다.
        consecutive_no_progress = 0
        for _ in range(_MAX_LOAD_MORE_CLICKS):
            more_button = page.get_by_text("더보기", exact=True)
            if more_button.count() == 0:
                break
            before = len(collected)
            try:
                more_button.first.scroll_into_view_if_needed()
                more_button.first.click(timeout=5_000)
            except PlaywrightTimeoutError:
                break
            page.wait_for_timeout(_LOAD_MORE_WAIT_MS)
            if len(collected) > before:
                consecutive_no_progress = 0
            else:
                consecutive_no_progress += 1
                if consecutive_no_progress >= _MAX_CONSECUTIVE_NO_PROGRESS:
                    break
    finally:
        page.remove_listener("response", _on_response)

    # 리뷰 목록 엔드포인트 응답을 한 번도 관측하지 못했다면 — URL 패턴 변경,
    # 클라이언트 사이드 404, 모든 요청이 인증 만료로 실패하는 경우 등 — 이건
    # "리뷰 0건"과 절대 같은 의미가 아니다. 조용히 빈 리스트를 반환하면 이번
    # 동기화 오류 신고 작업 전체가 무력화되므로 명시적으로 실패시킨다.
    if not state["observed_review_endpoint"]:
        raise BaeminScrapeError("리뷰 목록 API 응답을 한 번도 확인하지 못했습니다")

    # 리뷰 0건은 매장에 리뷰가 아직 없다는 뜻일 수도 있는 정상 데이터다 —
    # 엔드포인트 응답을 최소 한 번 관측했다면(위 체크 통과) 여기서는 에러로
    # 취급하지 않는다.
    return list(collected.values())


def map_review(raw: dict, store_id: int, platform_id: int, platform_shop_no: str) -> dict:
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
        "platform_shop_no": platform_shop_no,
        "status": "unanswered",
    }
