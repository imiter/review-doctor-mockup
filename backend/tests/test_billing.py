from datetime import date, datetime, timedelta, timezone

from app.models import Payment, Subscription
from app.plan import add_one_month, kst_today
from app.toss_client import TossConfirmError


def test_billing_me_basic_default(client, seeded_user, auth_headers):
    res = client.get("/billing/me", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "basic"
    assert body["is_pro"] is False
    assert body["daily_reply_limit"] == 10
    assert body["replies_used_today"] == 0


def test_checkout_ignores_client_amount_and_uses_server_price(client, db_session, seeded_user, auth_headers):
    res = client.post("/billing/checkout", json={}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["amount"] == 19900
    payment = db_session.query(Payment).filter_by(order_id=body["order_id"]).one()
    assert payment.status == "pending"
    assert payment.amount == 19900
    assert payment.user_id == seeded_user["user"].id


def test_confirm_success_upgrades_subscription(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(payment_key, order_id, amount):
        assert order_id == checkout["order_id"]
        assert amount == 19900
        return {"status": "DONE"}

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "test-pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["plan"] == "pro"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "pro"
    assert sub.expires_at == add_one_month(kst_today())

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "approved"
    assert payment.toss_payment_key == "test-pk"


def test_confirm_extends_existing_pro_period_instead_of_resetting(client, db_session, seeded_user, auth_headers, monkeypatch):
    future_expiry = kst_today() + timedelta(days=10)
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": future_expiry}
    )
    db_session.commit()

    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE"})

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk2", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.expires_at == add_one_month(future_expiry)  # 오늘이 아니라 기존 만료일부터 +1개월


def test_confirm_rejects_amount_mismatch_without_calling_toss(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    called = []
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: called.append(kw) or {"status": "DONE"})

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 1},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert called == []  # 토스 API 자체를 호출하지 않아야 함

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 건드리지 않음


def test_confirm_rejects_other_users_order_id(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from app.models import Store, Subscription as SubscriptionModel, User

    other_user = User(
        email="other@dris.kr", password_hash="x", nickname="다른사장",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()
    db_session.add(Payment(
        user_id=other_user.id, order_id="other-order", plan="pro", amount=19900,
        status="pending", requested_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE"})
    res = client.post(
        "/billing/confirm",
        json={"order_id": "other-order", "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 404


def test_confirm_toss_failure_marks_payment_failed_without_upgrading(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(**kw):
        raise TossConfirmError("카드사에서 결제를 거절했습니다")

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"
    assert "카드사" in payment.fail_reason

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"


def test_billing_history_returns_users_payments_newest_first(client, db_session, seeded_user, auth_headers):
    db_session.add_all([
        Payment(user_id=seeded_user["user"].id, order_id="old", plan="pro", amount=19900, status="approved",
                requested_at=datetime.now(timezone.utc) - timedelta(days=30)),
        Payment(user_id=seeded_user["user"].id, order_id="new", plan="pro", amount=19900, status="approved",
                requested_at=datetime.now(timezone.utc)),
    ])
    db_session.commit()

    res = client.get("/billing/history", headers=auth_headers)
    assert res.status_code == 200
    order_ids = [p["order_id"] for p in res.json()]
    assert order_ids == ["new", "old"]
