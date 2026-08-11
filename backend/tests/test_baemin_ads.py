from scrapers.baemin_ads import _should_count_click_metrics_response, map_click_metrics_by_date

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


# 코드 리뷰 지적사항(Task 3 fix round 1) 회귀 테스트: baemin_stats.py의
# _should_count_sales_response와 같은 버그 클래스 — discard됐어야 할 화면
# 진입 직후(예측 불가한 기본 달) 응답만으로 observed_any가 거짓 True가
# 되고, 그 응답 자체가 결과 리스트에 섞여 들어가던 문제. fetch_brand_click_metrics
# 자체는 Playwright가 필요해 pytest로 못 덮지만(이 파일의 Global
# Constraints), 실제 버그였던 판정 로직은 이 순수 함수로 뽑아내 직접
# 테스트할 수 있다.
def test_should_count_click_metrics_response_false_before_collecting_starts():
    # months 루프를 시작하기 전(화면 진입 직후, 예측 불가능한 기본 달)
    # 응답은 엔드포인트를 "관측했다"는 신호로도 인정하면 안 된다.
    assert _should_count_click_metrics_response(
        "/v2/statistics/campaign/cpc/metrics/14804912", 14804912, collecting=False
    ) is False


def test_should_count_click_metrics_response_true_once_collecting_started():
    # months 루프가 실제로 시작된 뒤(collecting=True)의 응답은 정상적으로
    # 관측 신호이자 수집 대상이다.
    assert _should_count_click_metrics_response(
        "/v2/statistics/campaign/cpc/metrics/14804912", 14804912, collecting=True
    ) is True


def test_should_count_click_metrics_response_false_for_different_shop_no_even_while_collecting():
    # 경로의 shop_no가 이 함수가 조회 중인 브랜드와 다르면(계정 내 다른
    # 브랜드 탭 등에서 우연히 뜬 응답) collecting 여부와 무관하게 False.
    assert _should_count_click_metrics_response(
        "/v2/statistics/campaign/cpc/metrics/99999999", 14804912, collecting=True
    ) is False
