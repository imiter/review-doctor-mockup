from scrapers.baemin_stats import (
    _should_count_sales_response,
    compute_repurchase_rates,
    map_deposits_by_date,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    map_settlement_breakdown_by_date,
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


def test_map_deposits_by_date_dedupes_same_give_id_across_pages():
    # 실 계정으로 재현된 실제 버그: 숫자 페이지네이션 경계에서 같은 배치
    # (giveId)가 두 페이지 응답 모두에 나타날 수 있다 — 중복으로 두 번
    # 합산되면 안 되고 한 번만 반영돼야 한다.
    page_a = {"contents": [
        {"giveId": 531969790, "depositDueDate": "2026-08-11", "giveAmount": 168431},
    ], "totalSize": 13}
    page_b = {"contents": [
        {"giveId": 531969790, "depositDueDate": "2026-08-11", "giveAmount": 168431},
        {"giveId": 531748522, "depositDueDate": "2026-08-10", "giveAmount": 293063},
    ], "totalSize": 13}
    result = map_deposits_by_date([page_a, page_b])
    assert result == {"2026-08-11": 168431, "2026-08-10": 293063}  # 168431이 두 번 더해지면 안 됨


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


# 실 계정(2026-08-12 조사)에서 직접 캡처한 GET
# /v3/settle/history/details/{giveId} 응답(giveId=531969790, 정산기간
# 26.08.07~26.08.09, 입금일 26-08-12)을 필요한 필드만 남겨 축약한 것.
# 검산: baemin1Details.giveAmount + baeminDetails.giveAmount +
# etcDetails.total + cpcDetails.total == 최상위 giveAmount (904812) —
# 실 계정으로 직접 확인 완료.
_SETTLE_DETAIL_531969790 = {
    "giveAmount": 904812,
    "baemin1Details": {
        "giveAmount": 936472,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -102741},
            "benefitsAmount": {"total": -266760},
        },
        "delivery": {"deliverySupplyPrice": {"total": -210800}},
        "extra": {"paymentFee": {"total": -27329}},
    },
    "baeminDetails": {
        "giveAmount": 16435,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -1081},
            "benefitsAmount": {"total": -5000},
        },
        "delivery": {"deliverySupplyPrice": {"total": 0}},
        "extra": {"paymentFee": {"total": -251}},
    },
    "etcDetails": {"total": 0},
    "cpcDetails": {"total": -48095},
}
_TAGGED_DETAIL = {"giveId": 531969790, "depositDueDate": "2026-08-12", **_SETTLE_DETAIL_531969790}


def test_map_settlement_breakdown_by_date_computes_four_categories():
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL])
    assert result == {
        "2026-08-12": {
            "commission_amount": 131_402,        # (102741+27329) + (1081+251)
            "delivery_fee_amount": 210_800,       # 210800 + 0
            "customer_discount_amount": 271_760,  # 266760 + 5000
            "ad_cost_amount": 48_095,             # -(-48095)
        }
    }


def test_map_settlement_breakdown_by_date_reconciles_with_top_level_give_amount():
    """검산: baemin1Details.giveAmount + baeminDetails.giveAmount +
    etcDetails.total + cpcDetails.total == 최상위 giveAmount. 이 fixture
    자체가 실 계정으로 검산된 값이라는 걸 보장하는 회귀 테스트."""
    d = _SETTLE_DETAIL_531969790
    total = (
        d["baemin1Details"]["giveAmount"] + d["baeminDetails"]["giveAmount"]
        + d["etcDetails"]["total"] + d["cpcDetails"]["total"]
    )
    assert total == d["giveAmount"] == 904_812


def test_map_settlement_breakdown_by_date_sums_multiple_batches_same_date():
    other = {
        "giveId": 111_111_111, "depositDueDate": "2026-08-12",
        "giveAmount": 10_000,
        "baemin1Details": {
            "giveAmount": 10_000,
            "orderBrokerage": {
                "serviceFeeAmount": {"total": -1_000},
                "benefitsAmount": {"total": -500},
            },
            "delivery": {"deliverySupplyPrice": {"total": -2_000}},
            "extra": {"paymentFee": {"total": -300}},
        },
        "baeminDetails": None,
        "etcDetails": {"total": 0},
        "cpcDetails": {"total": 0},
    }
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL, other])
    assert result["2026-08-12"]["commission_amount"] == 131_402 + 1_300   # 1000+300
    assert result["2026-08-12"]["delivery_fee_amount"] == 210_800 + 2_000
    assert result["2026-08-12"]["customer_discount_amount"] == 271_760 + 500
    assert result["2026-08-12"]["ad_cost_amount"] == 48_095


def test_map_settlement_breakdown_by_date_dedupes_same_give_id():
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL, _TAGGED_DETAIL])
    assert result["2026-08-12"]["ad_cost_amount"] == 48_095  # 두 번 더해지면 안 됨


def test_map_settlement_breakdown_by_date_skips_entries_without_deposit_due_date():
    orphan = {**_SETTLE_DETAIL_531969790, "giveId": 999, "depositDueDate": None}
    assert map_settlement_breakdown_by_date([orphan]) == {}


def test_map_settlement_breakdown_by_date_empty_list_returns_empty_dict():
    assert map_settlement_breakdown_by_date([]) == {}


from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from scrapers.baemin_stats import (
    compute_order_sync_range,
    map_order_rows,
    parse_applied_range,
    parse_baemin_datetime,
)

# 실 계정(2026-08-13 조사)에서 확인한 실제 GET /v4/orders
# contents[].order 필드 형태를 축약한 것. deliveryType은 실측 227건 중
# DELIVERY 223건, TAKEOUT 4건만 나왔다.
_ORDER_DELIVERY = {
    "order": {
        "orderNumber": "T2FE000020VQ",
        "orderDateTime": "2026-08-13T02:19:37",
        "payAmount": 15900,
        "itemsSummary": "[양념조절가능]숯불양념바베큐치킨",
        "deliveryType": "DELIVERY",
    },
}
_ORDER_TAKEOUT = {
    "order": {
        "orderNumber": "B2FD00HZNU",
        "orderDateTime": "2026-08-10T18:02:11",
        "payAmount": 21000,
        "itemsSummary": "[갓성비]1인 숯불양념치밥 SET",
        "deliveryType": "TAKEOUT",
    },
}
_ORDER_UNKNOWN_TYPE = {
    "order": {
        "orderNumber": "X9999999999",
        "orderDateTime": "2026-08-11T09:00:00",
        "payAmount": 9900,
        "itemsSummary": "알 수 없는 유형",
        "deliveryType": "SOMETHING_NEW",
    },
}


def test_map_order_rows_maps_delivery_type():
    result = map_order_rows([_ORDER_DELIVERY])
    assert result == [{
        "order_no": "T2FE000020VQ",
        "ordered_at": "2026-08-13T02:19:37",
        "menu_summary": "[양념조절가능]숯불양념바베큐치킨",
        "order_type": "delivery",
        "amount": 15900,
    }]


def test_map_order_rows_maps_takeout_type():
    result = map_order_rows([_ORDER_TAKEOUT])
    assert result[0]["order_type"] == "takeout"


def test_map_order_rows_skips_unknown_delivery_type():
    # 알려지지 않은 deliveryType은 그 주문만 건너뛴다 — 하드 에러 아님.
    result = map_order_rows([_ORDER_DELIVERY, _ORDER_UNKNOWN_TYPE, _ORDER_TAKEOUT])
    assert len(result) == 2
    assert {r["order_no"] for r in result} == {"T2FE000020VQ", "B2FD00HZNU"}


def test_map_order_rows_truncates_long_menu_summary():
    long_item = {
        "order": {
            "orderNumber": "T1234567890",
            "orderDateTime": "2026-08-13T10:00:00",
            "payAmount": 10000,
            "itemsSummary": "메" * 250,
            "deliveryType": "DELIVERY",
        },
    }
    result = map_order_rows([long_item])
    assert len(result[0]["menu_summary"]) == 200


def test_map_order_rows_empty_list_returns_empty_list():
    assert map_order_rows([]) == []


def test_compute_order_sync_range_no_existing_data_backfills_three_calendar_months():
    # 직관적인 경우: 2026-08-13 → 3개월 전 = 2026-05-13
    today = date(2026, 8, 13)
    start, end = compute_order_sync_range(None, today)
    assert end == today
    assert start == date(2026, 5, 13)


def test_compute_order_sync_range_no_existing_data_handles_year_rollover():
    # 연도 롤오버: 2026-02-15 → 3개월 전 = 2025-11-15
    today = date(2026, 2, 15)
    start, end = compute_order_sync_range(None, today)
    assert end == today
    assert start == date(2025, 11, 15)


def test_compute_order_sync_range_no_existing_data_clamps_day_for_short_month():
    # 일자 제약: 2026-05-31 → 3개월 전은 2월인데, 2월은 31일이 없으므로 28일로 조정
    today = date(2026, 5, 31)
    start, end = compute_order_sync_range(None, today)
    assert end == today
    assert start == date(2026, 2, 28)


def test_compute_order_sync_range_no_existing_data_clamps_day_in_leap_year():
    # 윤년 경우: 2024-05-31 → 3개월 전은 2월인데, 2024년은 윤년이므로 29일로 조정
    today = date(2024, 5, 31)
    start, end = compute_order_sync_range(None, today)
    assert end == today
    assert start == date(2024, 2, 29)


def test_compute_order_sync_range_with_existing_data_uses_two_day_buffer():
    today = date(2026, 8, 13)
    latest = datetime(2026, 8, 10, 15, 30, 0)
    start, end = compute_order_sync_range(latest, today)
    assert end == today
    assert start == date(2026, 8, 8)  # 8/10 - 2일


def test_compute_order_sync_range_converts_aware_cursor_to_kst():
    """`orders.ordered_at`은 TIMESTAMPTZ라 DB에서 읽으면 UTC 기준 aware
    datetime이다. 한국 새벽 주문(KST 00~09시)은 UTC로는 전날이라, 변환 없이
    `.date()`를 쓰면 커서가 하루 어긋난다 — KST로 변환한 날짜를 써야 한다."""
    today = date(2026, 8, 13)
    # 2026-08-12 17:19:37 UTC == 2026-08-13 02:19:37 KST (심야 치킨 주문)
    latest_utc = datetime(2026, 8, 12, 17, 19, 37, tzinfo=timezone.utc)
    start, end = compute_order_sync_range(latest_utc, today)
    assert end == today
    # KST 날짜(8/13) - 2일 = 8/11. UTC 날짜(8/12)를 썼다면 8/10이 됐을 것이다.
    assert start == date(2026, 8, 11)


def test_parse_applied_range_reads_same_year_range():
    """실측 확인된 표시 형태 — 범위는 연도가 왼쪽에만 나온다."""
    assert parse_applied_range("2026. 8. 7 ~ 8. 13") == ((2026, 8, 7), (2026, 8, 13))


def test_parse_applied_range_reads_single_date_as_same_start_and_end():
    """날짜 선택이 빗나가면 화면 표시가 하루짜리 단일 날짜가 된다(실측) —
    이 경우 시작=종료로 파싱돼 요청한 범위와 달라 재시도를 유발해야 한다."""
    assert parse_applied_range("2026. 8. 13") == ((2026, 8, 13), (2026, 8, 13))


def test_parse_applied_range_reads_cross_year_range():
    assert parse_applied_range("2025. 12. 30 ~ 2026. 1. 5") == ((2025, 12, 30), (2026, 1, 5))


def test_parse_applied_range_returns_none_for_unparseable_text():
    """모호하면 조용히 통과시키지 않는다 — None이면 호출자가 재시도하고,
    끝내 못 맞추면 하드 에러를 던진다."""
    assert parse_applied_range("") is None
    assert parse_applied_range("기간을 선택해 주세요") is None
    assert parse_applied_range("2026. 8 ~ 9") is None


def test_parse_baemin_datetime_attaches_kst_to_naive_wall_clock():
    """배민 `orderDateTime`은 오프셋 없는 한국 벽시계 시간이다 — TIMESTAMPTZ
    컬럼에 naive로 넣으면 Postgres가 UTC로 해석해 9시간 늦은 순간이 된다."""
    dt = parse_baemin_datetime("2026-08-13T02:19:37")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=9)
    # 절대 시각 왕복: 한국시간 8/13 02:19:37 == UTC 8/12 17:19:37
    assert dt.astimezone(timezone.utc) == datetime(2026, 8, 12, 17, 19, 37, tzinfo=timezone.utc)
    # 다시 한국시간으로 되돌리면 원래 벽시계 숫자가 그대로 나온다.
    seoul = dt.astimezone(ZoneInfo("Asia/Seoul"))
    assert (seoul.year, seoul.month, seoul.day, seoul.hour, seoul.minute, seoul.second) == (
        2026, 8, 13, 2, 19, 37,
    )


def test_parse_baemin_datetime_keeps_evening_order_on_the_same_korean_day():
    """15시 이후 주문(실측 데이터의 91%)은 naive로 저장하면 UTC 해석 때문에
    날짜까지 하루 밀린다 — 한국 날짜가 그대로 유지돼야 한다."""
    dt = parse_baemin_datetime("2026-08-13T21:40:00")
    assert dt.astimezone(timezone.utc) == datetime(2026, 8, 13, 12, 40, 0, tzinfo=timezone.utc)
    assert dt.astimezone(ZoneInfo("Asia/Seoul")).date() == date(2026, 8, 13)


def test_parse_baemin_datetime_respects_existing_offset():
    """이미 오프셋이 붙어 온 값은 절대 시각이 확정된 것이라 그대로 존중한다."""
    dt = parse_baemin_datetime("2026-08-13T02:19:37+00:00")
    assert dt.utcoffset() == timedelta(0)
    assert dt == datetime(2026, 8, 13, 2, 19, 37, tzinfo=timezone.utc)
