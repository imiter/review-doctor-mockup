"""ACoS(%) = CPC / (CVR × AOV) × 100. CVR은 반드시 소수 0~1로 계산한다."""

from app.acos import calculate_performance


def test_acos_matches_claude_md_example():
    # CVR 18.4%, 클릭당 단가 169원 예시(우가클 점수 화면 참고) 재현:
    # clicks=1000, ad_orders=184(→CVR=0.184), ad_spend=169000(→CPC=169)
    perf = calculate_performance(ad_spend=169_000, clicks=1000, ad_orders=184, ad_revenue=184 * 25_000)
    assert perf.cpc == 169.0
    assert perf.cvr == 0.184  # 소수 0~1, 퍼센트 아님
    assert perf.aov == 25_000.0
    # ACoS = 169 / (0.184 * 25000) * 100
    assert perf.acos == round(169 / (0.184 * 25_000) * 100, 2)


def test_acos_zero_clicks_returns_none():
    perf = calculate_performance(ad_spend=0, clicks=0, ad_orders=0, ad_revenue=0)
    assert perf.cpc == 0.0
    assert perf.cvr == 0.0
    assert perf.acos is None
    assert perf.score is None


def test_acos_zero_ad_orders_returns_none_acos():
    # 클릭은 있었지만 광고 경유 주문이 0건 → AOV 분모 0 → ACoS 계산 불가
    perf = calculate_performance(ad_spend=50_000, clicks=100, ad_orders=0, ad_revenue=0)
    assert perf.cpc == 500.0
    assert perf.cvr == 0.0
    assert perf.aov == 0.0
    assert perf.acos is None
    assert perf.score is None


def test_score_bands():
    # ACoS < 10% → 90점 이상
    high = calculate_performance(ad_spend=5_000, clicks=100, ad_orders=20, ad_revenue=500_000)
    assert high.acos < 10
    assert high.score >= 90

    # ACoS 25% 이상 → 개선 필요 (70점 미만)
    low = calculate_performance(ad_spend=100_000, clicks=100, ad_orders=5, ad_revenue=100_000)
    assert low.acos >= 25
    assert low.score < 70
