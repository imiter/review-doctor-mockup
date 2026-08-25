"""배민 사장님광장의 메뉴관리 화면에서 브랜드(shop_no)별 가게소개/원산지/
메뉴소개 텍스트와 전체 메뉴 항목(이름/설명/구성/가격)을 가져온다.

### 배경 (2026-08-26)

리뷰 답글 RAG 생성이 리뷰 내용과 무관한 답글을 쓴 사례가 실사용 중
확인됐다 — 별점은 높지만 "치킨마요는 밥만 많고 고기가 없다"는 불만에,
AI가 아니라 사장님이 직접 답글을 쓰면서도 "새로 나온 메뉴라..."처럼
틀린 추측을 했다. 원인을 보니 이 프로젝트에 애초에 "메뉴" 데이터가
전혀 없었다 — `generate_ai_reply`가 참고하는 건 리뷰 텍스트와 사장님
말투뿐, 실제로 그 메뉴가 뭘로 구성되는지는 전혀 모른다. 이 모듈은 그
그라운딩 데이터를 실제 배민에서 가져온다.

### 실 계정 실측 (2026-08-26, 치밥대장 4개 브랜드)

메뉴관리 화면(`/shops/{shopNo}/menu-management/menu-groups`)은 직접
`page.goto()`로 브랜드별 URL에 들어가면(다른 스크래핑처럼 사이드바 클릭이
필요 없다 — 로그인 세션 안에서 shop_no만 바꿔 바로 이동 가능함을 확인)
organic하게 두 응답을 발생시킨다:

    GET https://self-api.baemin.com/gateway/menu/v1/shops/{shopNo}
    → {"intro": "...", "menuInfo": {"foodOrigin": "...", "menuIntro": "..."}, ...}

    GET https://self-api.baemin.com/v1/menu-sys/core/v1/shop-owners/{ownerId}/menupans/{menupanId}
    → {"data": {"menuGroups": [{"menus": [{
         "menuName": "[꼬소한대창] 1인 순살 곱도리탕:",
         "menuDesc": "얼~큰한 국물과 부드러운 닭...",
         "menuComposition": "곱도리탕[대창+야채,감자/당면토핑]+공기밥+조미김",
         "menuPriceResponses": [{"price": 19900}],
         ...
       }, ...]}, ...]}}

`intro`/`menuInfo.foodOrigin`/`menuInfo.menuIntro`는 사장님이 배민에
직접 써둔 소개글이라 "100% 순살 닭다리살만 씁니다" 같은 실제 재료 사실이
이미 담겨있다(곱도리탕 브랜드로 실측 확인). `menuComposition`은 그
메뉴가 실제로 뭘로 구성되는지(예: 치킨마요에 닭이 얼마나 들어가는지)를
사장님 개입 없이 알 수 있는 유일한 소스다.

두 번째 URL의 `shop-owners/{ownerId}/menupans/{menupanId}` 두 값은
로그인 세션·브랜드마다 다르므로 우리가 직접 구성하지 않는다 — 페이지가
메뉴관리 화면에 진입할 때 스스로 만들어 보내는 organic 요청을
가로챌 뿐이다(다른 배민 스크래핑과 동일한 이유 — 직접 구성한 요청은
서명 헤더가 없어 차단됨, `baemin_reviews.py` 모듈 docstring 참고).
"""

from urllib.parse import urlparse


class BaeminMenuScrapeError(Exception):
    pass


def _dismiss_promo_modal(page) -> None:
    dismiss = page.get_by_text("오늘 하루 보지 않기", exact=False)
    if dismiss.count() > 0:
        dismiss.first.click()
        page.wait_for_timeout(500)


def map_menu_items(menupan_response: dict) -> list[dict]:
    """menupans/{id} 응답의 menuGroups를 우리 스키마의 평평한 메뉴 항목
    리스트로 변환한다. 가격이 없는(menuPriceResponses가 비어있는) 항목은
    price를 None으로 남긴다 — 실제로 그런 항목이 존재할 수 있다(예: 품절
    표시만 있고 가격 미설정)."""
    items = []
    for group in menupan_response.get("data", {}).get("menuGroups", []):
        for menu in group.get("menus", []):
            prices = menu.get("menuPriceResponses") or []
            items.append({
                "name": menu.get("menuName", "").strip(),
                "desc": menu.get("menuDesc") or "",
                "composition": menu.get("menuComposition") or "",
                "price": prices[0]["price"] if prices else None,
            })
    return items


def fetch_brand_menu_info(page, shop_no: int) -> dict:
    """로그인된 `page`로 브랜드(shop_no)의 메뉴관리 화면에 들어가 가게소개/
    원산지/메뉴소개와 전체 메뉴 항목을 가져온다. 두 organic 응답을 모두
    받지 못하면(레이아웃 변경, 인증 만료 등) 조용히 빈 값을 반환하지
    않고 명시적으로 실패시킨다 — 메뉴 정보가 통째로 비어버리면 답글
    생성이 아예 그라운딩 없이 도는 것보다 오히려 위험하기 때문이 아니라,
    "가져올 게 없었다"와 "가져오다 실패했다"를 구분해야 다음 동기화가
    재시도할지 판단할 수 있어서다."""
    shop_info: dict | None = None
    menupan: dict | None = None

    def _on_response(response) -> None:
        nonlocal shop_info, menupan
        path = urlparse(response.url).path
        if response.status != 200:
            return
        if path == f"/gateway/menu/v1/shops/{shop_no}":
            try:
                shop_info = response.json()
            except Exception:
                pass
            return
        # .../shop-owners/{ownerId}/menupans/{menupanId} — 끝이 딱 "menupans/숫자"인
        # 것만 받는다. 같은 접두사를 쓰는 .../menupans/{id}/menu-accept 같은 다른
        # 엔드포인트와 구분하기 위해서다.
        parts = path.rsplit("/", 2)
        if len(parts) == 3 and parts[1] == "menupans" and parts[2].isdigit():
            try:
                menupan = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/menu-management/menu-groups")
        except Exception as e:
            raise BaeminMenuScrapeError(f"메뉴관리 페이지 이동에 실패했습니다: {e}") from e
        page.wait_for_timeout(3_000)
        _dismiss_promo_modal(page)
        page.wait_for_timeout(2_000)
    finally:
        page.remove_listener("response", _on_response)

    if shop_info is None or menupan is None:
        raise BaeminMenuScrapeError(
            f"shop_no={shop_no}의 메뉴 정보 응답을 받지 못했습니다 "
            f"(shop_info={'O' if shop_info else 'X'}, menupan={'O' if menupan else 'X'})"
        )

    menu_info = shop_info.get("menuInfo") or {}
    return {
        "store_intro": shop_info.get("intro") or "",
        "food_origin": menu_info.get("foodOrigin") or "",
        "menu_intro": menu_info.get("menuIntro") or "",
        "menu_items": map_menu_items(menupan),
    }
