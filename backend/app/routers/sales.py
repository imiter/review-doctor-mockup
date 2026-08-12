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
    """매출분석 카드. 플랫폼별 매출 → 차감 항목 → 순정산액.

    배민 행은 정산 상세(수수료/배달비/고객할인/우가클비용) 동기화가 된
    기간이면 실측값(`is_estimate: False`)을 반환하고, 아직 없으면(신규 컬럼이 전부
    NULL) 요율 기반 추정치(`is_estimate: True`)로 폴백한다. 요기요/쿠팡이츠는
    실측 컬럼을 채우지 않으므로 항상 추정치다. "기타"(misc_amount)는
    저장하지 않고 sales_amount − 4개 실측 카테고리 − actual_deposit로
    계산한다(설계 문서 "데이터 모델 변경" 절 — 정규화 원칙, 서로 다른
    배민 화면 간 오차를 그대로 드러내는 게 의도된 동작).
    """
    sid = store_id or get_user_default_store_id(user, db)
    start, end = _period_range(period)

    rows = db.execute(
        select(
            Platform.id, Platform.name, Platform.default_commission_rate,
            func.coalesce(func.sum(DailySettlement.sales_amount), 0),
            func.coalesce(func.sum(DailySettlement.deposit_amount), 0),
            func.coalesce(func.sum(DailySettlement.commission_amount), 0),
            func.coalesce(func.sum(DailySettlement.delivery_fee_amount), 0),
            func.coalesce(func.sum(DailySettlement.customer_discount_amount), 0),
            func.coalesce(func.sum(DailySettlement.ad_cost_amount), 0),
            func.count(DailySettlement.commission_amount),
        )
        .join(DailySettlement, DailySettlement.platform_id == Platform.id)
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date.between(start, end))
        .group_by(Platform.id, Platform.name, Platform.default_commission_rate)
        .order_by(Platform.id)
    ).all()

    result = []
    for (platform_id, name, commission_rate, sales, actual_deposit,
         real_commission, real_delivery, real_discount, real_ad_cost, real_rows_count) in rows:
        sales = int(sales)
        actual_deposit = int(actual_deposit)
        # is_estimate는 "이 기간에 실측 컬럼이 하나라도 있으면 전부 실측"으로
        # 판정한다 — 기간이 review_sync.py의 정산 상세 동기화 창(최근
        # 30일, `detail_window_start = today - timedelta(days=30)`)보다
        # 넓어지면, 그 창 밖 날짜는 commission_amount 등이 NULL인 채로
        # coalesce(sum(...), 0)이 조용히 0을 더해 실측인 척하는 결과가 나올
        # 수 있다. 현재는 가장 넓은 period 옵션인 "month"가 아래
        # `_period_range`에서 29일(오늘 포함 30일)이라 그 창 안에 정확히
        # 들어와 무해하지만, 두 창 크기는 서로 맞물려 있다는 게 코드로는
        # 안 드러난다 — 둘 중 하나만 나중에 넓히면 이 불변조건이 조용히
        # 깨진다(2026-08-13, 최종 리뷰에서 문서화만 하기로 결정, 창 크기
        # 자체는 바꾸지 않음).
        is_estimate = real_rows_count == 0
        if is_estimate:
            rate = float(commission_rate or 0)
            commission = round(sales * rate)
            payment_fee = round(sales * PAYMENT_FEE_RATE)
            result.append({
                "platform_id": platform_id,
                "platform_name": name,
                "sales_amount": sales,
                "is_estimate": True,
                "commission_estimate": commission,
                "payment_fee_estimate": payment_fee,
                "net_estimate": sales - commission - payment_fee,
                "actual_deposit": actual_deposit,
            })
        else:
            commission = int(real_commission)
            delivery = int(real_delivery)
            discount = int(real_discount)
            ad_cost = int(real_ad_cost)
            misc = sales - commission - delivery - discount - ad_cost - actual_deposit
            result.append({
                "platform_id": platform_id,
                "platform_name": name,
                "sales_amount": sales,
                "is_estimate": False,
                "commission_amount": commission,
                "delivery_fee_amount": delivery,
                "customer_discount_amount": discount,
                "ad_cost_amount": ad_cost,
                "misc_amount": misc,
                "actual_deposit": actual_deposit,
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
