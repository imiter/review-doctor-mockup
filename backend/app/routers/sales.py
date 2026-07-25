"""매출/입금/재주문율 요약. daily_settlements·repurchase_metrics를 기간별로 집계한다.

정규화 원칙: 매출 요약을 별도 테이블에 저장하지 않고 항상 원본을 집계한다.
"""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import DailySettlement, RepurchaseMetric, User

router = APIRouter(tags=["sales"])

Period = Literal["day", "week", "month", "this_month"]


def _period_range(period: Period) -> tuple[date, date]:
    today = date.today()
    if period == "day":
        return today, today
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today - timedelta(days=29), today
    if period == "this_month":
        return today.replace(day=1), today
    raise ValueError(period)


@router.get("/sales/summary")
def sales_summary(
    period: Period = "day",
    store_id: int | None = None,
    platform_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    start, end = _period_range(period)
    stmt = select(func.coalesce(func.sum(DailySettlement.sales_amount), 0)).where(
        DailySettlement.store_id == sid,
        DailySettlement.settle_date.between(start, end),
    )
    if platform_id:
        stmt = stmt.where(DailySettlement.platform_id == platform_id)
    total = db.scalar(stmt)
    return {"period": period, "from_date": start.isoformat(), "to_date": end.isoformat(), "total_sales": int(total)}


@router.get("/deposits/summary")
def deposits_summary(
    period: Period = "day",
    store_id: int | None = None,
    platform_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    start, end = _period_range(period)
    stmt = select(func.coalesce(func.sum(DailySettlement.deposit_amount), 0)).where(
        DailySettlement.store_id == sid,
        DailySettlement.settle_date.between(start, end),
    )
    if platform_id:
        stmt = stmt.where(DailySettlement.platform_id == platform_id)
    total = db.scalar(stmt)
    return {"period": period, "from_date": start.isoformat(), "to_date": end.isoformat(), "total_deposit": int(total)}


@router.get("/repurchase/summary")
def repurchase_summary(
    store_id: int | None = None,
    platform_id: int | None = None,
    days: int = Query(30, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    stmt = (
        select(RepurchaseMetric)
        .where(RepurchaseMetric.store_id == sid, RepurchaseMetric.metric_date >= date.today() - timedelta(days=days))
        .order_by(RepurchaseMetric.metric_date)
    )
    if platform_id:
        stmt = stmt.where(RepurchaseMetric.platform_id == platform_id)
    rows = db.scalars(stmt).all()
    return [
        {
            "metric_date": r.metric_date.isoformat(),
            "new_orders": r.new_orders,
            "repeat_orders": r.repeat_orders,
            "rate_raw": float(r.rate_raw),
            "rate_adjusted": float(r.rate_adjusted),
        }
        for r in rows
    ]
