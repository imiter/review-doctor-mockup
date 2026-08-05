def test_signup_creates_user_store_and_subscription(client, platforms):
    res = client.post("/auth/signup", json={
        "email": "new@test.com", "password": "pw12345!", "nickname": "새사장",
        "phone": "010-1234-5678", "marketing_agreed": True,
    })
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "new@test.com"
    assert "access_token" in body

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["nickname"] == "새사장"

    stores = client.get("/stores", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert len(stores) == 1  # 가입 직후 빈 대시보드 방지용 기본 매장


def test_phone_never_stored_raw(client, platforms, db_session):
    from app.models import User

    client.post("/auth/signup", json={
        "email": "phonecheck@test.com", "password": "pw12345!", "nickname": "테스트",
        "phone": "010-9999-0000",
    })
    user = db_session.query(User).filter_by(email="phonecheck@test.com").one()
    assert user.phone_hash is not None
    assert user.phone_hash != "010-9999-0000"
    assert len(user.phone_hash) == 64  # SHA-256 hex


def test_duplicate_signup_rejected(client, platforms):
    payload = {"email": "dup@test.com", "password": "pw12345!", "nickname": "중복"}
    assert client.post("/auth/signup", json=payload).status_code == 201
    assert client.post("/auth/signup", json=payload).status_code == 409


def test_login_wrong_password_rejected(client, seeded_user):
    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong"})
    assert res.status_code == 401


def test_login_success_returns_token(auth_headers):
    assert "Authorization" in auth_headers


def test_protected_route_requires_token(client):
    assert client.get("/dashboard").status_code == 401


def test_protected_route_rejects_garbage_token(client):
    res = client.get("/dashboard", headers={"Authorization": "Bearer not-a-real-token"})
    assert res.status_code == 401


def test_update_profile_nickname(client, auth_headers):
    res = client.patch("/auth/me", json={"nickname": "새사장님"}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["nickname"] == "새사장님"

    me = client.get("/auth/me", headers=auth_headers).json()
    assert me["nickname"] == "새사장님"


def test_update_profile_phone_is_hashed_not_stored_raw(client, db_session, seeded_user, auth_headers):
    from app.models import User

    res = client.patch("/auth/me", json={"phone": "010-5555-6666"}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["has_phone"] is True

    user = db_session.query(User).filter_by(email="demo@dris.kr").one()
    assert user.phone_hash != "010-5555-6666"
    assert len(user.phone_hash) == 64


def test_update_profile_partial_does_not_clear_other_fields(client, auth_headers):
    client.patch("/auth/me", json={"marketing_agreed": True}, headers=auth_headers)
    res = client.patch("/auth/me", json={"nickname": "그대로"}, headers=auth_headers)
    assert res.json()["marketing_agreed"] is True  # 앞서 켠 값 유지


def test_social_account_links_user_and_enforces_unique_provider_pair(db_session):
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import SocialAccount, User

    user = User(
        email=None, password_hash=None, nickname="카카오전용",
        marketing_agreed=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(SocialAccount(
        user_id=user.id, provider="kakao", provider_user_id="9999",
        connected_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    found = db_session.query(SocialAccount).filter_by(provider="kakao", provider_user_id="9999").one()
    assert found.user_id == user.id

    db_session.add(SocialAccount(
        user_id=user.id, provider="kakao", provider_user_id="9999",
        connected_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
