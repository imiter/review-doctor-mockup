from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_db
from app.models import Order, OrderDeduction, Platform, Settlement, StorePlatform

router = APIRouter(prefix="/api", tags=["settlements"])


def _row(s: Settlement) -> dict:
    return {
        "id": s.id,
        "store_name": s.store_platform.store.name,
        "platform_name": s.store_platform.platform.name,
        "period_start": s.period_start.isoformat(),
        "period_end": s.period_end.isoformat(),
        "payout_date": s.payout_date.isoformat(),
        "total_gross": s.total_gross,
        "total_deductions": s.total_deductions,
        "net_payout": s.net_payout,
        "status": s.status,
    }


@router.get("/settlements")
def list_settlements(
    platform_code: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
):
    stmt = (
        select(Settlement)
        .options(
            joinedload(Settlement.store_platform).joinedload(StorePlatform.store),
            joinedload(Settlement.store_platform).joinedload(StorePlatform.platform),
        )
        .order_by(Settlement.period_start.desc())
    )
    if platform_code:
        stmt = (
            stmt.join(StorePlatform, Settlement.store_platform_id == StorePlatform.id)
            .join(Platform, StorePlatform.platform_id == Platform.id)
            .where(Platform.code == platform_code)
        )
    if from_date:
        stmt = stmt.where(Settlement.period_start >= from_date)
    if to_date:
        stmt = stmt.where(Settlement.period_end <= to_date)
    return [_row(s) for s in db.scalars(stmt).all()]


@router.get("/settlements/{settlement_id}")
def settlement_detail(settlement_id: int, db: Session = Depends(get_db)):
    s = db.scalar(
        select(Settlement)
        .options(
            joinedload(Settlement.store_platform).joinedload(StorePlatform.store),
            joinedload(Settlement.store_platform).joinedload(StorePlatform.platform),
            selectinload(Settlement.orders).selectinload(Order.deductions),
        )
        .where(Settlement.id == settlement_id)
    )
    if s is None:
        raise HTTPException(404, "정산 없음")
    by_type = db.execute(
        select(OrderDeduction.type, func.sum(OrderDeduction.amount))
        .join(Order, OrderDeduction.order_id == Order.id)
        .where(Order.settlement_id == s.id)
        .group_by(OrderDeduction.type)
    ).all()
    orders = [
        {
            "id": o.id,
            "order_no": o.order_no,
            "ordered_at": o.ordered_at.isoformat(),
            "item_amount": o.item_amount,
            "delivery_tip": o.delivery_tip,
            "deduction_total": sum(d.amount for d in o.deductions),
        }
        for o in s.orders
    ]
    return {**_row(s), "deductions_by_type": [{"type": t, "amount": a} for t, a in by_type], "orders": orders}
