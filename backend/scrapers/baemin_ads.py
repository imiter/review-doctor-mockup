"""배민 사장님광장의 "마케팅 성과 → 우리가게클릭" 화면(브랜드별 광고 클릭
성과) API 응답을 날짜별로 집계하는 순수 함수와, 그 organic 응답을 실제로
캡처하는 `fetch_brand_click_metrics`.

기존 `baemin_stats.py`(가게통계/정산내역/주문내역 — 계정 전체 합산 지표)와
관심사가 달라 별도 파일로 분리했다: 이 모듈이 다루는 "우리가게클릭"은
브랜드(shop_no) 단위로만 조회되는 화면이고, 계정 전체로 합산하지 않는다
(설계 문서의 "스코프 결정" 절 참고).
"""


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
