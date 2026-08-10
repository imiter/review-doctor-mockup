from scrapers.baemin_stats import (
    compute_repurchase_rates,
    map_deposits_by_date,
    map_repurchase_by_date,
    map_sales_by_date,
)

# 실 계정(치밥대장, 곱도리탕 등)에서 확인한 실제 statistics/orders/summary 응답 형태.
_SALES_RESPONSE_BRAND_A = {
    "graph": {"data": [
        {"x": "2026-07-30", "y": 60200.0},
        {"x": "2026-07-31", "y": 102800.0},
    ]},
    "orderAmount": 163000.0,
    "orderCount": 5,
}
_SALES_RESPONSE_BRAND_B = {
    "graph": {"data": [
        {"x": "2026-07-30", "y": 15000.0},
        {"x": "2026-07-31", "y": 0},
    ]},
    "orderAmount": 15000.0,
    "orderCount": 1,
}


def test_map_sales_by_date_sums_across_brands_for_same_date():
    result = map_sales_by_date([_SALES_RESPONSE_BRAND_A, _SALES_RESPONSE_BRAND_B])
    assert result == {"2026-07-30": 75200, "2026-07-31": 102800}


def test_map_sales_by_date_rounds_fractional_amounts():
    resp = {"graph": {"data": [{"x": "2026-07-30", "y": 100.4}]}, "orderAmount": 100.4, "orderCount": 1}
    assert map_sales_by_date([resp]) == {"2026-07-30": 100}


def test_map_sales_by_date_empty_list_returns_empty_dict():
    assert map_sales_by_date([]) == {}


# 실 계정에서 확인한 실제 settle/history/summary 응답 형태(페이지 2개로 흉내).
_SETTLE_PAGE_1 = {
    "foodSuccess": True, "commerceSuccess": True,
    "contents": [
        {"giveId": 531969790, "depositDueDate": "2026-08-12", "settleCode": "FOOD",
         "giveStatus": "REQUEST", "giveStartDate": "2026-08-07", "giveEndDate": "2026-08-09",
         "giveAmount": 904812},
        {"giveId": 531748522, "depositDueDate": "2026-08-11", "settleCode": "FOOD",
         "giveStatus": "REQUEST", "giveStartDate": "2026-08-06", "giveEndDate": "2026-08-06",
         "giveAmount": 168431},
    ],
    "totalSize": 3,
}
_SETTLE_PAGE_2 = {
    "foodSuccess": True, "commerceSuccess": True,
    "contents": [
        {"giveId": 531600000, "depositDueDate": "2026-08-12", "settleCode": "FOOD",
         "giveStatus": "GIVEN", "giveStartDate": "2026-08-05", "giveEndDate": "2026-08-05",
         "giveAmount": 50000},
    ],
    "totalSize": 3,
}


def test_map_deposits_by_date_sums_multiple_batches_sharing_same_due_date():
    # 08-12에 배치가 2건(904,812원 + 50,000원) 겹치는 상황 — 같은 날짜로 합산돼야 한다.
    result = map_deposits_by_date([_SETTLE_PAGE_1, _SETTLE_PAGE_2])
    assert result == {"2026-08-12": 954812, "2026-08-11": 168431}


def test_map_deposits_by_date_ignores_give_status():
    # 상태(예정/확정)는 구분하지 않고 giveAmount를 그대로 합산한다(설계 결정).
    result = map_deposits_by_date([_SETTLE_PAGE_2])
    assert result == {"2026-08-12": 50000}


def test_map_deposits_by_date_empty_contents_returns_empty_dict():
    assert map_deposits_by_date([{"contents": [], "totalSize": 0}]) == {}


# 실 계정에서 확인한 실제 crmInfo 응답의 newReorderSummary 형태(브랜드 2개).
_CRM_RESPONSE_BRAND_A = {
    "orderSummary": {"orderCount": 2, "orderPrice": 40200.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 1,
        "timeNewGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 1}, {"x": "2026-08-05", "y": 0},
        ]},
        "timeReorderGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 1},
        ]},
    },
}
_CRM_RESPONSE_BRAND_B = {
    "orderSummary": {"orderCount": 1, "orderPrice": 15000.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 0,
        "timeNewGraph": {"data": [
            {"x": "2026-08-03", "y": 1}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 0},
        ]},
        "timeReorderGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 0},
        ]},
    },
}


def test_map_repurchase_by_date_sums_new_and_repeat_across_brands():
    result = map_repurchase_by_date([_CRM_RESPONSE_BRAND_A, _CRM_RESPONSE_BRAND_B])
    assert result == {
        "2026-08-03": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-04": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-05": {"new_orders": 0, "repeat_orders": 1},
    }


def test_map_repurchase_by_date_empty_list_returns_empty_dict():
    assert map_repurchase_by_date([]) == {}


def test_compute_repurchase_rates_raw_is_same_day_ratio():
    by_date = {
        "2026-08-01": {"new_orders": 3, "repeat_orders": 1},
        "2026-08-02": {"new_orders": 1, "repeat_orders": 3},
    }
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-01"]["rate_raw"] == 0.25   # 1 / (3+1)
    assert result["2026-08-02"]["rate_raw"] == 0.75   # 3 / (1+3)


def test_compute_repurchase_rates_raw_is_zero_when_no_orders_that_day():
    by_date = {"2026-08-01": {"new_orders": 0, "repeat_orders": 0}}
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-01"]["rate_raw"] == 0.0


def test_compute_repurchase_rates_adjusted_sums_trailing_7_days_inclusive():
    # seed.sql의 Mock 생성 로직(ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)과
    # 동일한 "당일 포함 최근 7일" 윈도우여야 한다.
    by_date = {f"2026-08-{d:02d}": {"new_orders": 1, "repeat_orders": 0} for d in range(1, 9)}
    by_date["2026-08-08"] = {"new_orders": 0, "repeat_orders": 7}  # 8일째만 전부 재주문

    result = compute_repurchase_rates(by_date)
    # 8/8 기준 최근 7일 윈도우 = 8/2~8/8 (7일치): new=6(2~7일, 각 1건) + repeat=7(8일) = 13건 중 repeat 7건
    assert result["2026-08-08"]["rate_adjusted"] == round(7 / 13, 4)


def test_compute_repurchase_rates_adjusted_window_shrinks_near_start_of_data():
    # 데이터가 3일치뿐이면 윈도우는 그 3일 전체로 축소된다(7일 채우지 못해도 에러 아님).
    by_date = {
        "2026-08-01": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-02": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-03": {"new_orders": 0, "repeat_orders": 1},
    }
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-03"]["rate_adjusted"] == round(1 / 3, 4)  # 3일 전체: new=2, repeat=1
