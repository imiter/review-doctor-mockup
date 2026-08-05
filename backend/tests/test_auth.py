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


def test_kakao_login_creates_new_user_with_store_and_subscription(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="1001", nickname="카카오사장", email=None),
    )

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["nickname"] == "카카오사장"
    assert body["user"]["email"] is None

    stores = client.get("/stores", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert len(stores) == 1


def test_kakao_login_reuses_existing_social_account(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="2002", nickname="재로그인사장", email=None),
    )
    body = {"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"}

    first = client.post("/auth/kakao/callback", json=body).json()
    second = client.post("/auth/kakao/callback", json=body).json()

    assert first["user"]["id"] == second["user"]["id"]
    stores = client.get("/stores", headers={"Authorization": f"Bearer {second['access_token']}"}).json()
    assert len(stores) == 1  # 두 번째 로그인에서 매장이 또 생기면 안 됨


def test_kakao_login_links_to_existing_email_account(client, platforms, seeded_user, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="3003", nickname="김사장", email="demo@dris.kr"),
    )

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 200
    assert res.json()["user"]["id"] == seeded_user["user"].id

    stores = client.get("/stores", headers={"Authorization": f"Bearer {res.json()['access_token']}"}).json()
    assert len(stores) == 1  # 기존 계정에 연결됐을 뿐, 새 매장이 추가로 생기면 안 됨


def test_kakao_login_failure_returns_502(client, platforms, monkeypatch):
    from app.kakao import KakaoAuthError
    from app.routers import auth as auth_router

    def _raise(*args, **kwargs):
        raise KakaoAuthError("boom")

    monkeypatch.setattr(auth_router, "exchange_code_for_token", _raise)

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "bad-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 502


def test_kakao_only_user_cannot_login_with_password(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="4004", nickname="카카오전용", email="kakaoonly@test.com"),
    )
    client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )

    res = client.post("/auth/login", json={"email": "kakaoonly@test.com", "password": "anything"})
    assert res.status_code == 401
