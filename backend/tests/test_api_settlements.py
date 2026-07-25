from datetime import date, datetime

from app.models import Order, OrderDeduction, Settlement
from tests.test_models_reviews import make_sp


def setup_settlement(db_session):
    sp = make_sp(db_session)
    settlement = Settlement(
        store_platform_id=sp.id,
        period_start=date(2026, 7, 13), period_end=date(2026, 7, 19),
        payout_date=date(2026, 7, 22),
        total_gross=20000, total_deductions=4524, net_payout=15476,
        status="paid",
    )
    db_session.add(settlement)
    db_session.flush()
    order = Order(
        store_platform_id=sp.id, settlement_id=settlement.id,
        order_no="BA20260713-0001", ordered_at=datetime(2026, 7, 13, 18, 30),
        item_amount=18000, delivery_tip=2000, status="completed",
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        OrderDeduction(order_id=order.id, type="platform_commission", amount=1224),
        OrderDeduction(order_id=order.id, type="delivery_fee", amount=3300),
    ])
    db_session.commit()
    return settlement


def test_list_settlements_with_filter(client, db_session):
    setup_settlement(db_session)
    assert len(client.get("/api/settlements").json()) == 1
    assert len(client.get("/api/settlements?platform_code=baemin").json()) == 1
    assert len(client.get("/api/settlements?platform_code=yogiyo").json()) == 0
    assert len(client.get("/api/settlements?from_date=2026-07-14").json()) == 0


def test_settlement_detail_breakdown(client, db_session):
    settlement = setup_settlement(db_session)
    res = client.get(f"/api/settlements/{settlement.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["net_payout"] == 15476
    by_type = {d["type"]: d["amount"] for d in body["deductions_by_type"]}
    assert by_type == {"platform_commission": 1224, "delivery_fee": 3300}
    assert body["orders"][0]["deduction_total"] == 4524
