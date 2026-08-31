from datetime import date, datetime, timezone

from app.models import Payment, User


def test_user_role_defaults_to_owner(db_session):
    user = User(nickname="테스트", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.role == "owner"


def test_auth_me_includes_role(client, seeded_user, auth_headers):
    res = client.get("/auth/me", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["role"] == "owner"


def _promote_to_admin(db_session, user: User) -> None:
    user.role = "admin"
    db_session.commit()


def test_admin_payments_requires_admin_role(client, seeded_user, auth_headers):
    res = client.get("/admin/payments", headers=auth_headers)
    assert res.status_code == 403


def test_admin_payments_lists_recent_payments(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    db_session.add(Payment(
        user_id=seeded_user["user"].id, order_id="order-1", plan="pro", amount=19900,
        status="approved", requested_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.get("/admin/payments", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-1"
    assert body[0]["user_email"] == "demo@dris.kr"
    assert body[0]["status"] == "approved"


def test_admin_payments_filters_by_status(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    db_session.add_all([
        Payment(user_id=seeded_user["user"].id, order_id="order-a", plan="pro", amount=19900,
                status="approved", requested_at=datetime.now(timezone.utc)),
        Payment(user_id=seeded_user["user"].id, order_id="order-b", plan="pro", amount=19900,
                status="failed", requested_at=datetime.now(timezone.utc), fail_reason="카드 한도 초과"),
    ])
    db_session.commit()

    res = client.get("/admin/payments?status=failed", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-b"
    assert body[0]["fail_reason"] == "카드 한도 초과"
