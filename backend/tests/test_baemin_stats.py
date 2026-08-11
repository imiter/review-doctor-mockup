from scrapers.baemin_stats import (
    _should_count_sales_response,
    compute_repurchase_rates,
    map_deposits_by_date,
    map_orders_to_daily_sales,
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


# 실 계정(치밥대장/블랙닭갈비 등)에서 확인한 실제 GET /v4/orders 응답의
# contents[] 항목 형태(fix round, fetch_current_month_orders로 이번 달
# 1일~오늘 범위를 조회해 캡처, 무관한 필드는 생략).
_ORDER_ITEM_1 = {
    "order": {
        "orderNumber": "T2FB0001H1UC", "status": "CLOSED",
        "orderDateTime": "2026-08-10T22:19:10", "payAmount": 34400,
        "shopNumber": 14804914,
    },
}
_ORDER_ITEM_2 = {
    "order": {
        "orderNumber": "T2FB0001FQS0", "status": "CLOSED",
        "orderDateTime": "2026-08-10T21:48:55", "payAmount": 18200,
        "shopNumber": 14804914,
    },
}
_ORDER_ITEM_3 = {
    "order": {
        "orderNumber": "T2FB0002ABCD", "status": "CLOSED",
        "orderDateTime": "2026-08-01T12:03:44", "payAmount": 15000,
        "shopNumber": 14804912,
    },
}


def test_map_orders_to_daily_sales_sums_pay_amount_by_date_across_brands():
    # 브랜드(shopNumber)가 달라도 같은 날짜면 합산된다 -- 주문내역 화면 자체가
    # 계정 전체(여러 브랜드)를 한 번의 조회로 합쳐서 준다(fetch_current_month_orders
    # docstring 참고).
    result = map_orders_to_daily_sales([_ORDER_ITEM_1, _ORDER_ITEM_2, _ORDER_ITEM_3])
    assert result == {"2026-08-10": 52600, "2026-08-01": 15000}


def test_map_orders_to_daily_sales_takes_only_date_portion_of_datetime():
    # "2026-08-10T22:19:10" 같은 ISO datetime에서 앞 10글자(YYYY-MM-DD)만 쓴다.
    item = {"order": {"orderDateTime": "2026-08-10T22:19:10", "payAmount": 100}}
    assert map_orders_to_daily_sales([item]) == {"2026-08-10": 100}


def test_map_orders_to_daily_sales_empty_list_returns_empty_dict():
    assert map_orders_to_daily_sales([]) == {}


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


# 코드 리뷰 지적사항(2026-08-11) 회귀 테스트: discard된 첫 로드 응답만으로
# observed_sales_endpoint가 거짓 True가 되던 버그. `fetch_shop_stats`
# 자체는 Playwright가 필요해 pytest로 못 덮지만(이 파일의 Global Constraints),
# 실제 버그였던 판정 로직은 이 순수 함수로 뽑아내 직접 테스트할 수 있다.
def test_should_count_sales_response_false_before_collecting_starts():
    # months 루프를 시작하기 전(첫 로드 시점, 예측 불가능한 달) 응답은
    # 매출 엔드포인트를 "관측했다"는 신호로도 인정하면 안 된다 — 그래야
    # months 전체가 배민의 실제 선택 가능 목록과 하나도 안 맞아 전부
    # 건너뛰어지는 상황에서 fetch_shop_stats가 조용히 성공한 것처럼
    # ([], crm_responses)를 반환하지 않고 제대로 에러를 낸다.
    assert _should_count_sales_response("/v3/statistics/orders/summary", collecting=False) is False


def test_should_count_sales_response_true_once_collecting_started():
    # months 루프가 실제로 시작된 뒤(collect_sales=True)의 응답은 정상적으로
    # 관측 신호이자 수집 대상이다.
    assert _should_count_sales_response("/v3/statistics/orders/summary", collecting=True) is True


def test_should_count_sales_response_false_for_unrelated_path_even_while_collecting():
    # 경로 자체가 매출 엔드포인트가 아니면 collecting 여부와 무관하게 False.
    assert _should_count_sales_response("/v3/dashboard/crmInfo", collecting=True) is False
