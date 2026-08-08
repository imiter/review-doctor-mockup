"""대시보드 요약. 각 지표는 해당 도메인의 원본 테이블을 그 자리에서 집계한다."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acos import calculate_performance
from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import (
    AdCampaign,
    AdPerformanceMetric,
    Alert,
    DailySettlement,
    RepurchaseMetric,
    Review,
    Store,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(store_id: int | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    today = date.today()

    sales_today = db.scalar(
        select(func.coalesce(func.sum(DailySettlement.sales_amount), 0))
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date == today)
    )
    deposit_today = db.scalar(
        select(func.coalesce(func.sum(DailySettlement.deposit_amount), 0))
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date == today)
    )

    unanswered = db.scalar(
        select(func.count(Review.id))
        .where(Review.store_id == sid, Review.status == "unanswered")
    )

    latest_repurchase = db.scalar(
        select(RepurchaseMetric)
        .where(RepurchaseMetric.store_id == sid)
        .order_by(RepurchaseMetric.metric_date.desc())
        .limit(1)
    )

    primary_campaign = db.scalar(
        select(AdCampaign).where(AdCampaign.store_id == sid).order_by(AdCampaign.id).limit(1)
    )
    ad_performance = None
    if primary_campaign:
        since = today - timedelta(days=14)
        agg = db.execute(
            select(
                func.coalesce(func.sum(AdPerformanceMetric.ad_spend), 0),
                func.coalesce(func.sum(AdPerformanceMetric.clicks), 0),
                func.coalesce(func.sum(AdPerformanceMetric.ad_orders), 0),
                func.coalesce(func.sum(AdPerformanceMetric.ad_revenue), 0),
            ).where(AdPerformanceMetric.campaign_id == primary_campaign.id, AdPerformanceMetric.metric_date >= since)
        ).one()
        perf = calculate_performance(*agg)
        ad_performance = {
            "campaign_id": primary_campaign.id,
            "category": primary_campaign.category,
            "acos": perf.acos,
            "score": perf.score,
        }

    unread_alerts = db.scalar(
        select(func.count(Alert.id)).where(Alert.store_id == sid, Alert.is_read.is_(False))
    )

    return {
        "store": {"id": store.id, "name": store.name, "category": store.category},
        "sales_today": int(sales_today),
        "deposit_today": int(deposit_today),
        "unanswered_reviews": unanswered,
        "repurchase_rate_adjusted": float(latest_repurchase.rate_adjusted) if latest_repurchase else None,
        "ad_performance": ad_performance,
        "unread_alerts": unread_alerts,
    }


@router.get("/stores")
def list_stores(user=Depends(get_current_user), db: Session = Depends(get_db)):
    stores = db.scalars(select(Store).where(Store.user_id == user.id).order_by(Store.id)).all()
    return [{"id": s.id, "name": s.name, "category": s.category} for s in stores]


@router.get("/alerts")
def list_alerts(store_id: int | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sid = store_id or get_user_default_store_id(user, db)
    alerts = db.scalars(
        select(Alert).where(Alert.store_id == sid).order_by(Alert.created_at.desc())
    ).all()
    return [
        {"id": a.id, "alert_type": a.alert_type, "message": a.message, "is_read": a.is_read, "created_at": a.created_at.isoformat()}
        for a in alerts
    ]
