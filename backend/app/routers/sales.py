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
from app.models import DailySettlement, Platform, RepurchaseMetric, User

router = APIRouter(tags=["sales"])

Period = Literal["day", "week", "month", "this_month"]

# seed.sql이 입금액을 만들 때 쓰는 결제수수료율과 반드시 일치시킨다 (매출분석 카드의 추정치 근거).
PAYMENT_FEE_RATE = 0.03


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


@router.get("/sales/daily")
def sales_daily(
    store_id: int | None = None,
    platform_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    since = date.today() - timedelta(days=days - 1)
    stmt = (
        select(DailySettlement.settle_date, func.sum(DailySettlement.sales_amount))
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date >= since)
        .group_by(DailySettlement.settle_date)
        .order_by(DailySettlement.settle_date)
    )
    if platform_id:
        stmt = stmt.where(DailySettlement.platform_id == platform_id)
    rows = db.execute(stmt).all()
    return [{"date": d.isoformat(), "amount": int(amount)} for d, amount in rows]


@router.get("/deposits/daily")
def deposits_daily(
    store_id: int | None = None,
    platform_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    since = date.today() - timedelta(days=days - 1)
    stmt = (
        select(DailySettlement.settle_date, func.sum(DailySettlement.deposit_amount))
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date >= since)
        .group_by(DailySettlement.settle_date)
        .order_by(DailySettlement.settle_date)
    )
    if platform_id:
        stmt = stmt.where(DailySettlement.platform_id == platform_id)
    rows = db.execute(stmt).all()
    return [{"date": d.isoformat(), "amount": int(amount)} for d, amount in rows]


@router.get("/sales/breakdown")
def sales_breakdown(
    period: Period = "week",
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """매출분석 카드. 플랫폼별 매출 → 중개수수료·결제수수료 추정 → 순정산액.

    중개수수료는 platforms.default_commission_rate(실제 저장값), 결제수수료는
    PAYMENT_FEE_RATE(seed 생성 공식과 동일)로 추정한 것이며 실제 입금액(deposit_amount)과는
    정산 주기(D+3)만큼 시차가 있어 완전히 일치하지 않을 수 있다 — 이 차이 자체가
    "매출과 입금이 다르다"는 현장 문제를 보여준다.
    """
    sid = store_id or get_user_default_store_id(user, db)
    start, end = _period_range(period)

    rows = db.execute(
        select(
            Platform.id, Platform.name, Platform.default_commission_rate,
            func.coalesce(func.sum(DailySettlement.sales_amount), 0),
            func.coalesce(func.sum(DailySettlement.deposit_amount), 0),
        )
        .join(DailySettlement, DailySettlement.platform_id == Platform.id)
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date.between(start, end))
        .group_by(Platform.id, Platform.name, Platform.default_commission_rate)
        .order_by(Platform.id)
    ).all()

    result = []
    for platform_id, name, commission_rate, sales, actual_deposit in rows:
        rate = float(commission_rate or 0)
        commission = round(sales * rate)
        payment_fee = round(sales * PAYMENT_FEE_RATE)
        result.append({
            "platform_id": platform_id,
            "platform_name": name,
            "sales_amount": int(sales),
            "commission_estimate": commission,
            "payment_fee_estimate": payment_fee,
            "net_estimate": int(sales) - commission - payment_fee,
            "actual_deposit": int(actual_deposit),
        })
    return {"period": period, "from_date": start.isoformat(), "to_date": end.isoformat(), "platforms": result}


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
