from datetime import date, datetime, timedelta, timezone

from app.models import Payment, Subscription
from app.plan import add_one_month, kst_today
from app.toss_client import TossConfirmError, TossTransportError


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
        return {"status": "DONE", "totalAmount": 19900}

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
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE", "totalAmount": 19900})

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
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: called.append(kw) or {"status": "DONE", "totalAmount": 19900})

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 1},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert called == []  # 토스 API 자체를 호출하지 않아야 함

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 건드리지 않음


def test_confirm_second_call_for_same_order_id_does_not_double_extend(client, db_session, seeded_user, auth_headers, monkeypatch):
    """동시성 레이스(TOCTOU) 회귀 테스트: 같은 order_id로 confirm이 두 번 들어와도
    두 번째 호출이 status != "pending" 체크에 걸려 거부되고, 구독이 1회분만 연장돼야 한다.
    실제 동시 요청(멀티스레드)은 이 하네스로 재현하기 어려워 순차 호출로 대신 검증한다."""
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE", "totalAmount": 19900})

    first = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk1", "amount": 19900},
        headers=auth_headers,
    )
    assert first.status_code == 200
    first_expires_at = first.json()["expires_at"]

    second = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk2", "amount": 19900},
        headers=auth_headers,
    )
    assert second.status_code == 400

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.expires_at.isoformat() == first_expires_at  # 두 번째 호출로 추가 연장되지 않음

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.toss_payment_key == "pk1"  # 두 번째 payment_key로 덮어써지지 않음


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

    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE", "totalAmount": 19900})
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


def test_confirm_rejects_when_toss_status_is_not_done(client, db_session, seeded_user, auth_headers, monkeypatch):
    """status가 DONE도 WAITING_FOR_DEPOSIT(가상계좌 입금 대기)도 아닌 응답(예: ABORTED)은
    HTTP 200이라도 승인 실패로 처리해야 한다 — 돈을 실제로 받았다는 확인 없이 Pro로 올리면 안 된다.
    (WAITING_FOR_DEPOSIT 전용 분기는 test_confirm_waiting_for_deposit_does_not_fail_or_approve 참고)"""
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    monkeypatch.setattr(
        "app.routers.billing.confirm_payment",
        lambda **kw: {"status": "ABORTED", "totalAmount": 19900},
    )

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"  # Pro로 올라가면 안 됨


def test_confirm_transport_error_leaves_payment_pending_for_retry(client, db_session, seeded_user, auth_headers, monkeypatch):
    """네트워크 타임아웃/설정 오류 등 토스한테 물어보지도 못한 상황은 진짜 거절과
    달리 payment.status를 "failed"로 확정하면 안 된다 — pending으로 남겨서 재시도 가능하게."""
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(**kw):
        raise TossTransportError("토스 API 호출 실패: timeout")

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 503
    assert "TOSS_SECRET_KEY" not in res.text  # 내부 설정값 이름이 사용자 메시지에 노출되면 안 됨

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 재시도 가능한 상태로 남아있어야 함

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"


def test_confirm_waiting_for_deposit_does_not_fail_or_approve(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(payment_key, order_id, amount):
        return {
            "status": "WAITING_FOR_DEPOSIT",
            "totalAmount": 19900,
            "virtualAccount": {
                "secret": "va-secret-abc",
                "bankCode": "20",
                "accountNumber": "1234567890",
                "dueDate": "2026-08-25T23:59:59",
            },
        }

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "va-pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "waiting_for_deposit"
    assert body["account_number"] == "1234567890"

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 실패도 승인도 아님 — 웹훅을 기다리는 중
    assert payment.virtual_account_secret == "va-secret-abc"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"  # 아직 안 바뀜


def test_confirm_still_rejects_non_done_non_waiting_status(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    monkeypatch.setattr(
        "app.routers.billing.confirm_payment",
        lambda **kw: {"status": "ABORTED", "totalAmount": 19900},
    )

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402
    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"


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


def _make_waiting_payment(db_session, seeded_user, order_id="va-order-1", secret="va-secret-abc"):
    payment = Payment(
        user_id=seeded_user["user"].id, order_id=order_id, plan="pro", amount=19900,
        status="pending", virtual_account_secret=secret,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(payment)
    db_session.commit()
    return payment


def test_webhook_approves_subscription_on_correct_secret_and_done_status(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user)

    res = client.post("/billing/webhook", json={"orderId": "va-order-1", "secret": "va-secret-abc", "status": "DONE"})
    assert res.status_code == 200

    payment = db_session.query(Payment).filter_by(order_id="va-order-1").one()
    assert payment.status == "approved"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "pro"


def test_webhook_ignores_wrong_secret(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-2")

    res = client.post("/billing/webhook", json={"orderId": "va-order-2", "secret": "wrong-secret", "status": "DONE"})
    assert res.status_code == 200  # 조용히 무시 — 4xx 안 줌

    payment = db_session.query(Payment).filter_by(order_id="va-order-2").one()
    assert payment.status == "pending"  # 안 바뀜


def test_webhook_ignores_unknown_order_id(client):
    res = client.post("/billing/webhook", json={"orderId": "no-such-order", "secret": "x", "status": "DONE"})
    assert res.status_code == 200


def test_webhook_is_idempotent_for_already_approved_payment(client, db_session, seeded_user):
    payment = _make_waiting_payment(db_session, seeded_user, order_id="va-order-3")
    from app.routers.billing import _approve_payment
    _approve_payment(payment, db_session)
    db_session.commit()
    sub_before = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    expires_before = sub_before.expires_at

    # 같은 웹훅이 중복 발송돼도 구독이 또 연장되면 안 됨
    res = client.post("/billing/webhook", json={"orderId": "va-order-3", "secret": "va-secret-abc", "status": "DONE"})
    assert res.status_code == 200

    sub_after = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub_after.expires_at == expires_before


def test_webhook_marks_failed_on_cancel_status(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-4")

    res = client.post("/billing/webhook", json={"orderId": "va-order-4", "secret": "va-secret-abc", "status": "CANCELED"})
    assert res.status_code == 200

    payment = db_session.query(Payment).filter_by(order_id="va-order-4").one()
    assert payment.status == "failed"


def test_confirm_second_call_after_waiting_for_deposit_does_not_recall_toss(client, db_session, seeded_user, auth_headers, monkeypatch):
    """입금 대기 화면 새로고침/StrictMode 이중마운트 회귀 테스트: 이미 virtual_account_secret이
    채워진(=한 번 WAITING_FOR_DEPOSIT 응답을 받은) 결제에 대해 confirm()이 다시 호출되면,
    토스 confirm API를 재호출하지 않고(재호출하면 ALREADY_PROCESSED_PAYMENT로 거절당해 결제가
    failed로 확정돼버린다) 같은 대기 응답을 그대로 돌려줘야 한다."""
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    payment.virtual_account_secret = "already-issued-secret"
    db_session.commit()

    called = []
    monkeypatch.setattr(
        "app.routers.billing.confirm_payment",
        lambda **kw: called.append(kw) or {"status": "DONE", "totalAmount": 19900},
    )

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "second-pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "waiting_for_deposit"
    assert called == []  # 토스 API를 다시 호출하지 않아야 함

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"


def test_confirm_waiting_for_deposit_rejects_amount_mismatch(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    monkeypatch.setattr(
        "app.routers.billing.confirm_payment",
        lambda **kw: {
            "status": "WAITING_FOR_DEPOSIT",
            "totalAmount": 1,
            "virtualAccount": {"secret": "va-secret", "bankCode": "20", "accountNumber": "123", "dueDate": "2026-08-25"},
        },
    )

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"


def test_webhook_rejects_non_ascii_secret_without_500(client, db_session, seeded_user):
    """hmac.compare_digest는 str 인자에 비ASCII 문자가 있으면 TypeError를 던진다.
    이게 그대로 500으로 새면 "이 order_id가 존재하고 가상계좌 상태다"를 알아내는
    오라클이 된다 — 반드시 조용한 200으로 처리돼야 한다."""
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-nonascii")

    res = client.post(
        "/billing/webhook",
        json={"orderId": "va-order-nonascii", "secret": "시크릿é", "status": "DONE"},
    )
    assert res.status_code == 200

    payment = db_session.query(Payment).filter_by(order_id="va-order-nonascii").one()
    assert payment.status == "pending"  # 상태 변화 없음


def test_webhook_missing_status_field_returns_200(client):
    """status 필드가 아예 없는 요청도(Pydantic 필수 필드였다면 422였을 것) 조용히 200을
    돌려줘야 한다 — 토스가 다른 이벤트 구조를 이 URL로 보내도 재시도 폭탄이 나지 않도록."""
    res = client.post("/billing/webhook", json={"orderId": "x", "secret": "y"})
    assert res.status_code == 200


def test_webhook_completely_different_shape_returns_200(client):
    res = client.post("/billing/webhook", json={"eventType": "payout.changed", "data": {}})
    assert res.status_code == 200


def test_webhook_logs_warning_on_secret_mismatch(client, db_session, seeded_user, caplog):
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-logtest")

    with caplog.at_level("WARNING"):
        res = client.post(
            "/billing/webhook",
            json={"orderId": "va-order-logtest", "secret": "wrong", "status": "DONE"},
        )
    assert res.status_code == 200
    assert any("secret 불일치" in r.message and "va-order-logtest" in r.message for r in caplog.records)
