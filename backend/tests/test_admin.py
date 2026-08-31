from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Payment, ReplySetting, ReviewSyncJob, Store, StorePlatformConnection, User


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


def test_admin_stores_only_includes_baemin_connections_with_credentials(
    client, db_session, seeded_user, platforms, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    # seeded_user가 이미 만들어둔 연결은 credential_ciphertext가 NULL이라 제외돼야 한다
    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == []


def test_admin_stores_includes_latest_sync_job_and_auto_reply_state(
    client, db_session, seeded_user, platforms, reply_styles, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    conn = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == store.id)
    )
    conn.credential_ciphertext = "encrypted-blob"
    db_session.add(ReplySetting(
        store_id=store.id, style_id=reply_styles.id, auto_reply_enabled=True, auto_reply_min_rating=5,
    ))
    db_session.add(ReviewSyncJob(
        store_id=store.id, platform_id=platforms["baemin"].id, status="success", triggered_by="scheduled",
        reviews_fetched=3, reviews_inserted=1,
        started_at=datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 30, 4, 5, tzinfo=timezone.utc),
    ))
    # 더 최근 실패 잡 — 이게 "최신"으로 선택돼야 한다
    db_session.add(ReviewSyncJob(
        store_id=store.id, platform_id=platforms["baemin"].id, status="failed", triggered_by="manual",
        error_message="매장 목록을 확인하지 못했습니다",
        started_at=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()

    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    row = body[0]
    assert row["store_name"] == store.name
    assert row["owner_email"] == "demo@dris.kr"
    assert row["auto_reply_enabled"] is True
    assert row["last_sync"]["status"] == "failed"
    assert row["last_sync"]["triggered_by"] == "manual"
    assert row["last_sync"]["error_message"] == "매장 목록을 확인하지 못했습니다"


def test_admin_stores_handles_store_with_no_sync_history(
    client, db_session, seeded_user, platforms, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]
    conn = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == store.id)
    )
    conn.credential_ciphertext = "encrypted-blob"
    db_session.commit()

    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["last_sync"] is None
    assert body[0]["auto_reply_enabled"] is False  # reply_settings 행이 아예 없을 때의 기본값


def test_admin_toggle_auto_reply_updates_setting(
    client, db_session, seeded_user, reply_styles, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]
    rs = ReplySetting(store_id=store.id, style_id=reply_styles.id, auto_reply_enabled=True, auto_reply_min_rating=5)
    db_session.add(rs)
    db_session.commit()

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": False}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is False

    db_session.refresh(rs)
    assert rs.auto_reply_enabled is False


def test_admin_toggle_auto_reply_404_when_no_settings(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": True}, headers=auth_headers,
    )
    assert res.status_code == 404


def test_admin_users_search_by_email_or_nickname(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    other = User(
        email="another@example.com", nickname="다른사장",
        password_hash="x", marketing_agreed=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.commit()

    res = client.get("/admin/users?q=demo", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["email"] == "demo@dris.kr"
    assert body[0]["plan"] == "basic"
    assert body[0]["store_count"] == 1


def test_admin_users_no_query_returns_recent_users(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.get("/admin/users", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json()) >= 1


def test_admin_set_plan_to_pro_sets_expires_at_from_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 14},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "pro"
    assert body["expires_at"] == str(date.today() + timedelta(days=14))


def test_admin_set_plan_to_pro_defaults_to_30_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro"}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["expires_at"] == str(date.today() + timedelta(days=30))


def test_admin_set_plan_to_basic_clears_expires_at(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro", "days": 30}, headers=auth_headers,
    )

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "basic"}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["plan"] == "basic"
    assert res.json()["expires_at"] is None


def test_admin_set_plan_rejects_out_of_range_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 400},
        headers=auth_headers,
    )
    assert res.status_code == 422


def test_admin_set_plan_404_for_unknown_user(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch("/admin/users/999999/plan", json={"plan": "pro"}, headers=auth_headers)
    assert res.status_code == 404


def test_admin_set_plan_rejects_invalid_plan_value(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "enterprise"}, headers=auth_headers,
    )
    assert res.status_code == 422


@pytest.mark.parametrize("path", ["/admin/payments", "/admin/stores", "/admin/users"])
def test_admin_get_endpoints_reject_non_admin(client, seeded_user, auth_headers, path):
    res = client.get(path, headers=auth_headers)
    assert res.status_code == 403


def test_admin_toggle_auto_reply_rejects_non_admin(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/stores/{seeded_user['store'].id}/auto-reply",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert res.status_code == 403


def test_admin_set_plan_rejects_non_admin(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro"},
        headers=auth_headers,
    )
    assert res.status_code == 403
