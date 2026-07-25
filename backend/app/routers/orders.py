from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import Order, Platform, User

router = APIRouter(tags=["orders"])


@router.get("/orders")
def list_orders(
    store_id: int | None = None,
    platform_id: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    stmt = (
        select(Order)
        .where(Order.store_id == sid)
        .options(joinedload(Order.platform))
        .order_by(Order.ordered_at.desc())
    )
    if platform_id:
        stmt = stmt.where(Order.platform_id == platform_id)
    if from_date:
        stmt = stmt.where(Order.ordered_at >= from_date)
    if to_date:
        stmt = stmt.where(Order.ordered_at < to_date)

    orders = db.scalars(stmt).all()
    return [
        {
            "id": o.id,
            "order_no": o.order_no,
            "platform_name": o.platform.name,
            "platform_color": o.platform.brand_color,
            "ordered_at": o.ordered_at.isoformat(),
            "menu_summary": o.menu_summary,
            "order_type": o.order_type,
            "amount": o.amount,
        }
        for o in orders
    ]


@router.get("/platforms")
def list_platforms(db: Session = Depends(get_db)):
    platforms = db.scalars(select(Platform).order_by(Platform.id)).all()
    return [{"id": p.id, "code": p.code, "name": p.name, "brand_color": p.brand_color} for p in platforms]
