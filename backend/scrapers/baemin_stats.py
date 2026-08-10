"""배민 사장님광장의 가게통계(매출/재주문율)·정산내역(입금) API 응답을
날짜별로 집계하는 순수 함수. 실제 organic 응답 캡처는 이 파일이 아니라
`fetch_shop_stats`/`fetch_account_settlement`(다음 태스크에서 추가)가
담당한다 — 이 모듈은 이미 받아온 raw dict만 다룬다.

브랜드(치밥대장 등)별로 분리하지 않고 전부 날짜 단위로 합산한다 — 설계
결정(계정 전체 합산만 지원, 입금이 애초에 브랜드별로 안 나뉘어 나오기
때문). 세 함수 모두 여러 브랜드/여러 달/여러 페이지에 걸친 raw 응답
리스트를 받아 하나의 날짜별 dict로 합친다.
"""


def map_sales_by_date(responses: list[dict]) -> dict[str, int]:
    """`GET /v3/statistics/orders/summary` 응답들의 `graph.data[].{x,y}`를
    날짜별로 합산한다. 브랜드마다, 그리고 월마다 한 번씩 호출한 응답을 전부
    이 리스트에 담아 넘긴다."""
    totals: dict[str, int] = {}
    for resp in responses:
        for point in resp["graph"]["data"]:
            date_str = point["x"]
            totals[date_str] = totals.get(date_str, 0) + round(point["y"])
    return totals


def map_deposits_by_date(responses: list[dict]) -> dict[str, int]:
    """`GET /v3/settle/history/summary` 응답들의 `contents[].{depositDueDate,
    giveAmount}`를 날짜별로 합산한다. 같은 depositDueDate에 배치가 여러 건
    겹치면 합산한다. `giveStatus`(예정/확정)는 구분하지 않는다(설계 결정) —
    상태와 무관하게 금액을 그대로 더한다. 페이지네이션이 있으면 여러 페이지
    응답을 전부 이 리스트에 담아 넘긴다."""
    totals: dict[str, int] = {}
    for resp in responses:
        for batch in resp["contents"]:
            date_str = batch["depositDueDate"]
            totals[date_str] = totals.get(date_str, 0) + batch["giveAmount"]
    return totals


def map_repurchase_by_date(responses: list[dict]) -> dict[str, dict[str, int]]:
    """`GET /v3/dashboard/crmInfo` 응답들의
    `newReorderSummary.timeNewGraph`/`timeReorderGraph`(각각 날짜별 신규/재주문
    건수)를 날짜별로 합산한다. 브랜드마다 한 번씩 호출한 응답을 전부 이
    리스트에 담아 넘긴다."""
    totals: dict[str, dict[str, int]] = {}

    def _bucket(date_str: str) -> dict[str, int]:
        return totals.setdefault(date_str, {"new_orders": 0, "repeat_orders": 0})

    for resp in responses:
        summary = resp["newReorderSummary"]
        for point in summary["timeNewGraph"]["data"]:
            _bucket(point["x"])["new_orders"] += point["y"]
        for point in summary["timeReorderGraph"]["data"]:
            _bucket(point["x"])["repeat_orders"] += point["y"]
    return totals


def compute_repurchase_rates(by_date: dict[str, dict[str, int]]) -> dict[str, dict]:
    """날짜별 new_orders/repeat_orders 집계에서 rate_raw(당일 비율)와
    rate_adjusted(당일 포함 최근 7일 합산 비율)를 계산한다. seed.sql의 Mock
    생성 로직(`ROWS BETWEEN 6 PRECEDING AND CURRENT ROW`)과 동일한 윈도우
    정의를 쓴다 — repurchase_metrics 스키마 주석의 "보정 후 = 이전 7일 합산"
    문구가 가리키는 바로 그 정의."""
    sorted_dates = sorted(by_date.keys())
    result: dict[str, dict] = {}
    for i, d in enumerate(sorted_dates):
        new_orders = by_date[d]["new_orders"]
        repeat_orders = by_date[d]["repeat_orders"]
        total = new_orders + repeat_orders
        rate_raw = round(repeat_orders / total, 4) if total > 0 else 0.0

        window_dates = sorted_dates[max(0, i - 6):i + 1]
        window_new = sum(by_date[wd]["new_orders"] for wd in window_dates)
        window_repeat = sum(by_date[wd]["repeat_orders"] for wd in window_dates)
        window_total = window_new + window_repeat
        rate_adjusted = round(window_repeat / window_total, 4) if window_total > 0 else 0.0

        result[d] = {
            "new_orders": new_orders,
            "repeat_orders": repeat_orders,
            "rate_raw": rate_raw,
            "rate_adjusted": rate_adjusted,
        }
    return result
