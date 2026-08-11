"""배민 사장님광장의 "마케팅 성과 → 우리가게클릭" 화면(브랜드별 광고 클릭
성과) API 응답을 날짜별로 집계하는 순수 함수와, 그 organic 응답을 실제로
캡처하는 `fetch_brand_click_metrics`.

기존 `baemin_stats.py`(가게통계/정산내역/주문내역 — 계정 전체 합산 지표)와
관심사가 달라 별도 파일로 분리했다: 이 모듈이 다루는 "우리가게클릭"은
브랜드(shop_no) 단위로만 조회되는 화면이고, 계정 전체로 합산하지 않는다
(설계 문서의 "스코프 결정" 절 참고).
"""

from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def map_click_metrics_by_date(responses: list[dict]) -> dict[str, dict]:
    """`GET /v2/statistics/campaign/cpc/metrics/{shopNumber}` 응답들의
    `dailyMetrics[].{date, spentBudget, displayCount, clickCount, orderCount,
    orderAmounts}`를 날짜별 dict로 모은다. 브랜드 하나에 대해 여러 달을
    호출한 응답을 전부 이 리스트에 담아 넘긴다 — 달마다 서로 다른 날짜를
    다루므로 겹칠 일이 없다(같은 달을 두 번 호출하지 않는 한)."""
    result: dict[str, dict] = {}
    for resp in responses:
        for day in resp.get("dailyMetrics", []):
            result[day["date"]] = {
                "ad_spend": day["spentBudget"],
                "impressions": day["displayCount"],
                "clicks": day["clickCount"],
                "ad_orders": day["orderCount"],
                "ad_revenue": day["orderAmounts"],
            }
    return result


class BaeminAdsScrapeError(Exception):
    pass


def _dismiss_backdrop_if_present(page) -> None:
    if page.get_by_test_id("backdrop").count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def _select_click_metrics_month(page, month: str) -> None:
    """화면에 보이는 "N월"(현재 선택된 달) 라벨을 클릭해 "기간" 다이얼로그를
    열고, 그 안의 네이티브 `<select>`로 `month`("YYYY-MM")를 고른 뒤 "적용"을
    누른다. 라벨 텍스트는 현재 선택된 달에 따라 바뀌므로("8월", "6월" 등)
    정규식으로 "N월" 형태를 찾는다."""
    import re

    month_label = page.get_by_text(re.compile(r"^\d+월$"), exact=True)
    month_label.first.click(timeout=5_000)
    page.wait_for_timeout(1_000)

    select_el = page.locator("select").last
    select_el.select_option(value=month, timeout=5_000)
    page.wait_for_timeout(500)

    apply_btn = page.get_by_role("button", name="적용")
    apply_btn.first.click(timeout=5_000)
    page.wait_for_timeout(2_000)


def fetch_brand_click_metrics(page, shop_no: int, months: list[str]) -> list[dict]:
    """브랜드(shop_no)의 "마케팅 성과 → 우리가게클릭" 화면에서
    `/v2/statistics/campaign/cpc/metrics/{shopNumber}` organic 응답을
    `months`에 담긴 각 달마다 캡처한다. 계정 전체가 아니라 브랜드 하나에
    대해서만 호출한다 — 호출자가 `session.shops`를 순회하며 브랜드마다
    이 함수를 부른다."""
    responses: list[dict] = []
    observed = {"any": False}

    def _on_response(response) -> None:
        if urlparse(response.url).path != f"/v2/statistics/campaign/cpc/metrics/{shop_no}":
            return
        observed["any"] = True
        if response.status == 200:
            try:
                responses.append(response.json())
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/stat/marketing/woori-shop-click")
        except Exception as e:
            raise BaeminAdsScrapeError(f"우리가게클릭 성과 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(4_000)
        _dismiss_backdrop_if_present(page)

        # 콘텐츠가 스켈레톤 상태로 몇 초간 유지될 수 있어, "N월" 라벨이
        # 실제로 나타날 때까지 최대 15초 폴링한다(고정 대기만으로는 첫
        # 로드 시점에 따라 부족할 수 있음을 실측으로 확인했다).
        import re
        for _ in range(15):
            if page.get_by_text(re.compile(r"^\d+월$"), exact=True).count() > 0:
                break
            page.wait_for_timeout(1_000)

        for month in months:
            try:
                _select_click_metrics_month(page, month)
            except PlaywrightTimeoutError as e:
                raise BaeminAdsScrapeError(f"{month} 우리가게클릭 성과 조회 중 월 선택에 실패했습니다: {e}") from e
    finally:
        page.remove_listener("response", _on_response)

    if not observed["any"]:
        raise BaeminAdsScrapeError("우리가게클릭 성과 API 응답을 한 번도 확인하지 못했습니다")

    return responses
