"""광고 성과(ACoS 실계산) + 광고 순위 모니터링(Mock 스냅샷). 실제 CPC 자동입찰·순위 크롤링 없음."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acos import calculate_performance
from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, Order, User

router = APIRouter(tags=["ads"])


@router.get("/ads/performance")
def ads_performance(
    store_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()
    since = date.today() - timedelta(days=days)

    total_orders = db.scalar(
        select(func.count(Order.id)).where(Order.store_id == sid, Order.ordered_at >= since)
    )

    result = []
    for c in campaigns:
        agg = db.execute(
            select(
                func.coalesce(func.sum(AdPerformanceMetric.ad_spend), 0),
                func.coalesce(func.sum(AdPerformanceMetric.clicks), 0),
                func.coalesce(func.sum(AdPerformanceMetric.ad_orders), 0),
                func.coalesce(func.sum(AdPerformanceMetric.ad_revenue), 0),
            ).where(AdPerformanceMetric.campaign_id == c.id, AdPerformanceMetric.metric_date >= since)
        ).one()
        perf = calculate_performance(*agg)
        order_share = round(perf.ad_orders / total_orders, 4) if total_orders else None
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "status": c.status,
            "period_days": days,
            "ad_spend": perf.ad_spend,
            "clicks": perf.clicks,
            "ad_orders": perf.ad_orders,
            "ad_revenue": perf.ad_revenue,
            "cpc": perf.cpc,
            "cvr": perf.cvr,
            "aov": perf.aov,
            "acos": perf.acos,
            "score": perf.score,
            "order_share": order_share,
        })
    return result


@router.get("/ads/rank-monitoring")
def ads_rank_monitoring(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()

    result = []
    for c in campaigns:
        latest = db.scalar(
            select(AdRankSnapshot)
            .where(AdRankSnapshot.campaign_id == c.id)
            .order_by(AdRankSnapshot.snapshot_at.desc())
            .limit(1)
        )
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "target_rank": c.target_rank,
            "status": c.status,
            "current_rank": latest.current_rank if latest else None,
            "competitor_est_cpc": latest.competitor_est_cpc if latest else None,
            "rank_status": latest.status if latest else None,
            "recommended_action": latest.recommended_action if latest else "keep",
            "suggested_cpc": latest.suggested_cpc if latest else None,
            "snapshot_at": latest.snapshot_at.isoformat() if latest else None,
        })
    return result
