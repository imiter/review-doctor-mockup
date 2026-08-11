from scrapers.baemin_ads import map_click_metrics_by_date

# 실 계정(치밥대장, 2026-08-12 조사)에서 확인한 실제
# /v2/statistics/campaign/cpc/metrics/{shopNumber} 응답 형태.
_AUGUST_RESPONSE = {
    "summary": {"displayCount": 201, "clickCount": 6, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 2.985, "orderRate": 0.0, "spentBudget": 570, "returnOnAdSpend": 0.0},
    "metrics": {"displayCount": [], "clickCount": [], "orderCount": [], "orderAmounts": []},
    "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 95, "displayCount": 40, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
        {"date": "2026-08-02", "spentBudget": 95, "displayCount": 12, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
    ],
}
_JUNE_RESPONSE = {
    "summary": {"displayCount": 649, "clickCount": 31, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 4.776, "orderRate": 0.0, "spentBudget": 2945, "returnOnAdSpend": 0.0},
    "metrics": {"displayCount": [], "clickCount": [], "orderCount": [], "orderAmounts": []},
    "dailyMetrics": [
        {"date": "2026-06-01", "spentBudget": 100, "displayCount": 20, "clickCount": 1,
         "orderCount": 1, "orderAmounts": 15000, "returnOnAdSpend": 150.0},
    ],
}


def test_map_click_metrics_by_date_extracts_daily_fields():
    result = map_click_metrics_by_date([_AUGUST_RESPONSE])
    assert result == {
        "2026-08-01": {"ad_spend": 95, "impressions": 40, "clicks": 1, "ad_orders": 0, "ad_revenue": 0},
        "2026-08-02": {"ad_spend": 95, "impressions": 12, "clicks": 1, "ad_orders": 0, "ad_revenue": 0},
    }


def test_map_click_metrics_by_date_merges_multiple_months_no_overlap():
    result = map_click_metrics_by_date([_AUGUST_RESPONSE, _JUNE_RESPONSE])
    assert set(result.keys()) == {"2026-08-01", "2026-08-02", "2026-06-01"}
    assert result["2026-06-01"] == {
        "ad_spend": 100, "impressions": 20, "clicks": 1, "ad_orders": 1, "ad_revenue": 15000,
    }


def test_map_click_metrics_by_date_empty_list_returns_empty_dict():
    assert map_click_metrics_by_date([]) == {}


def test_map_click_metrics_by_date_response_with_no_daily_metrics_key_contributes_nothing():
    # 캠페인이 없는 브랜드 등, 방어적으로 dailyMetrics가 없는 응답이 섞여도
    # 죽지 않고 그 응답만 건너뛴다.
    assert map_click_metrics_by_date([{"summary": {}, "metrics": {}}]) == {}
