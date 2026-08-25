import pytest

from scrapers.baemin_menu import BaeminMenuScrapeError, fetch_brand_menu_info, map_menu_items

# 실 계정(곱도리탕 브랜드, 2026-08-26)에서 확인한 실제 menupans 응답 형태를 축약한 것.
_MENUPAN_RESPONSE = {
    "data": {
        "menuGroups": [
            {
                "menuGroupId": 1005389397,
                "menuGroupName": "::1인 순살 닭볶음탕&곱도리탕::",
                "menus": [
                    {
                        "menuId": 1031919104,
                        "menuName": "[꼬소한대창] 1인 순살 곱도리탕:",
                        "menuDesc": "얼큰한 국물과 부드러운 닭, 고소한 대창의 조합",
                        "menuComposition": "곱도리탕[대창+야채,감자/당면토핑]+공기밥+조미김",
                        "menuPriceResponses": [{"price": 19900}],
                    },
                    {
                        "menuId": 1031919105,
                        "menuName": "[혼밥]1인 순살 닭도리탕:",
                        "menuDesc": "부드러운 순살의 매력",
                        "menuComposition": "닭도리탕[야채,감자/당면토핑]+공기밥+조미김",
                        "menuPriceResponses": [],  # 가격 미설정 케이스
                    },
                ],
            },
        ],
    },
}

_SHOP_INFO_RESPONSE = {
    "intro": "안녕하세요.\n곱도리탕 진짜 잘하는집 입니다!\n\n[저희 매장의 3가지 원칙]\n2. 100% 순살 닭다리살만 씁니다.",
    "menuInfo": {
        "foodOrigin": "닭고기(브라질산)감자(국내산)",
        "menuIntro": "야들야들한 100% 순살 닭다리살\n부드러운 식감의 비법의 연육염지닭 사용",
    },
}


def test_map_menu_items_extracts_name_desc_composition_price():
    items = map_menu_items(_MENUPAN_RESPONSE)
    assert items[0] == {
        "name": "[꼬소한대창] 1인 순살 곱도리탕:",
        "desc": "얼큰한 국물과 부드러운 닭, 고소한 대창의 조합",
        "composition": "곱도리탕[대창+야채,감자/당면토핑]+공기밥+조미김",
        "price": 19900,
    }


def test_map_menu_items_handles_missing_price():
    items = map_menu_items(_MENUPAN_RESPONSE)
    assert items[1]["price"] is None


def test_map_menu_items_returns_empty_list_for_empty_groups():
    assert map_menu_items({"data": {"menuGroups": []}}) == []


class _FakeDismissLocator:
    def __init__(self, present=False):
        self.present = present
        self.click_calls = 0

    def count(self):
        return 1 if self.present else 0

    @property
    def first(self):
        return self

    def click(self):
        self.click_calls += 1


class _FakeResponse:
    def __init__(self, url, status, body):
        self.url = url
        self.status = status
        self._body = body

    def json(self):
        return self._body


class _FakePage:
    def __init__(self, *, shop_info=None, menupan=None, goto_raises=None):
        self.goto_calls = []
        self._shop_info = shop_info
        self._menupan = menupan
        self._goto_raises = goto_raises
        self._handlers = {}
        self.dismiss = _FakeDismissLocator(present=False)

    def goto(self, url):
        self.goto_calls.append(url)
        if self._goto_raises:
            raise self._goto_raises

    def wait_for_timeout(self, ms):
        pass

    def get_by_text(self, text, exact=False):
        return self.dismiss

    def on(self, event, handler):
        self._handlers[event] = handler

    def remove_listener(self, event, handler):
        self._handlers.pop(event, None)

    def fire_organic_responses(self, shop_no):
        handler = self._handlers["response"]
        if self._shop_info is not None:
            handler(_FakeResponse(f"https://self-api.baemin.com/gateway/menu/v1/shops/{shop_no}", 200, self._shop_info))
        if self._menupan is not None:
            handler(_FakeResponse(
                "https://self-api.baemin.com/v1/menu-sys/core/v1/shop-owners/202509300151/menupans/1000724858",
                200, self._menupan,
            ))
            # menu-accept 같은 유사 경로는 무시돼야 한다(회귀 확인용으로 같이 쏴본다)
            handler(_FakeResponse(
                "https://self-api.baemin.com/v1/menu-sys/core/v1/shop-owners/202509300151/menupans/1000724858/menu-accept",
                200, {"data": {"unexpected": True}},
            ))


def test_fetch_brand_menu_info_combines_shop_info_and_menupan(monkeypatch):
    page = _FakePage(shop_info=_SHOP_INFO_RESPONSE, menupan=_MENUPAN_RESPONSE)

    import scrapers.baemin_menu as menu_mod
    real_goto = page.goto

    def _goto_and_fire(url):
        real_goto(url)
        page.fire_organic_responses(14804912)

    monkeypatch.setattr(page, "goto", _goto_and_fire)

    result = fetch_brand_menu_info(page, shop_no=14804912)

    assert "100% 순살 닭다리살" in result["store_intro"]
    assert result["food_origin"] == "닭고기(브라질산)감자(국내산)"
    assert "연육염지닭" in result["menu_intro"]
    assert len(result["menu_items"]) == 2
    assert page.goto_calls == ["https://self.baemin.com/shops/14804912/menu-management/menu-groups"]


def test_fetch_brand_menu_info_ignores_menu_accept_response(monkeypatch):
    """menu-accept 응답이 menupan으로 잘못 파싱되면 안 된다."""
    page = _FakePage(shop_info=_SHOP_INFO_RESPONSE, menupan=_MENUPAN_RESPONSE)

    def _goto_and_fire(url):
        page.goto_calls.append(url)
        page.fire_organic_responses(14804912)

    monkeypatch.setattr(page, "goto", _goto_and_fire)

    result = fetch_brand_menu_info(page, shop_no=14804912)
    assert len(result["menu_items"]) == 2  # menu-accept의 {"unexpected": True}가 아니라 진짜 menupan만 반영됨


def test_fetch_brand_menu_info_raises_when_responses_never_arrive(monkeypatch):
    page = _FakePage(shop_info=None, menupan=None)

    def _goto_only(url):
        page.goto_calls.append(url)

    monkeypatch.setattr(page, "goto", _goto_only)

    with pytest.raises(BaeminMenuScrapeError, match="응답을 받지 못했습니다"):
        fetch_brand_menu_info(page, shop_no=14804912)


def test_fetch_brand_menu_info_raises_when_goto_fails():
    page = _FakePage(goto_raises=RuntimeError("네트워크 오류"))
    with pytest.raises(BaeminMenuScrapeError, match="페이지 이동에 실패"):
        fetch_brand_menu_info(page, shop_no=14804912)
