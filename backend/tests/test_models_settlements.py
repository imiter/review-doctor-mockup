from datetime import date, datetime

from app.models import Order, OrderDeduction, Settlement
from tests.test_models_reviews import make_sp


def test_order_deductions_and_settlement_link(db_session):
    sp = make_sp(db_session)
    settlement = Settlement(
        store_platform_id=sp.id,
        period_start=date(2026, 7, 13), period_end=date(2026, 7, 19),
        payout_date=date(2026, 7, 22),
        total_gross=20000, total_deductions=5000, net_payout=15000,
        status="paid",
    )
    db_session.add(settlement)
    db_session.flush()

    order = Order(
        store_platform_id=sp.id, settlement_id=settlement.id,
        order_no="B20260713-001", ordered_at=datetime(2026, 7, 13, 18, 30),
        item_amount=18000, delivery_tip=2000, status="completed",
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        OrderDeduction(order_id=order.id, type="platform_commission", amount=1224),
        OrderDeduction(order_id=order.id, type="delivery_fee", amount=3300),
    ])
    db_session.flush()

    assert len(order.deductions) == 2
    assert settlement.orders[0].order_no == "B20260713-001"
