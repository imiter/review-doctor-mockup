"""광고비율(ACoS) 계산 — 이 프로젝트에서 실제 공식으로 계산하는 유일한 로직.

ACoS(%) = CPC / (CVR × AOV) × 100
  CPC = ad_spend ÷ clicks        (클릭당 광고비)
  CVR = ad_orders ÷ clicks       (전환율, 소수 0~1. 18.4% = 0.184)
  AOV = ad_revenue ÷ ad_orders   (평균 객단가)

노출수·CTR은 광고비/광고매출 양쪽에서 약분되므로 세 값(ad_spend, clicks,
ad_orders, ad_revenue)만으로 계산한다.

점수화는 CLAUDE.md에 명시된 추정 구간이며, 실측 데이터가 아니다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AdPerformance:
    ad_spend: int
    clicks: int
    ad_orders: int
    ad_revenue: int
    cpc: float
    cvr: float
    aov: float
    acos: float | None  # None = 계산 불가 (분모 0)
    score: int | None


def calculate_performance(
    ad_spend: int, clicks: int, ad_orders: int, ad_revenue: int
) -> AdPerformance:
    cpc = ad_spend / clicks if clicks > 0 else 0.0
    cvr = ad_orders / clicks if clicks > 0 else 0.0  # 소수 0~1
    aov = ad_revenue / ad_orders if ad_orders > 0 else 0.0

    denominator = cvr * aov
    acos = (cpc / denominator) * 100 if denominator > 0 else None
    score = _score_from_acos(acos) if acos is not None else None

    return AdPerformance(
        ad_spend=ad_spend,
        clicks=clicks,
        ad_orders=ad_orders,
        ad_revenue=ad_revenue,
        cpc=round(cpc, 2),
        cvr=round(cvr, 4),
        aov=round(aov, 2),
        acos=round(acos, 2) if acos is not None else None,
        score=score,
    )


def _score_from_acos(acos: float) -> int:
    """ACoS 구간별 점수 (추정 가정, CLAUDE.md 명시).

    < 10%  : 90~100점 (ACoS 0%일 때 100점, 10%일 때 90점 — 선형)
    10~15% : 80~90점대
    15~25% : 70~80점대
    >= 25% : 70점 미만, 개선 필요 (60점 하한)
    """
    if acos < 10:
        return round(100 - acos * 1.0)
    if acos < 15:
        return round(90 - (acos - 10) * 2.0)
    if acos < 25:
        return round(80 - (acos - 15) * 1.0)
    return max(60, round(70 - (acos - 25) * 0.5))
