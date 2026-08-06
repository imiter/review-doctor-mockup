def test_signup_creates_user_store_and_subscription(client, platforms, signup_flow):
    res = signup_flow("new@test.com", phone="010-1234-5678", nickname="새사장", marketing_agreed=True)
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "new@test.com"
    assert "access_token" in body

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["nickname"] == "새사장"

    stores = client.get("/stores", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert len(stores) == 1  # 가입 직후 빈 대시보드 방지용 기본 매장


def test_phone_never_stored_raw(client, platforms, db_session, signup_flow):
    from app.models import User

    signup_flow("phonecheck@test.com", phone="010-9999-0000")
    user = db_session.query(User).filter_by(email="phonecheck@test.com").one()
    assert user.phone_hash is not None
    assert user.phone_hash != "010-9999-0000"
    assert len(user.phone_hash) == 64  # SHA-256 hex


def test_duplicate_signup_rejected(client, platforms, signup_flow):
    signup_flow("dup@test.com")
    # signup_flow의 사전 확인 호출(email-code/phone-code)은 이제 200을 요구하므로,
    # "이미 가입된 이메일" 케이스는 email-code 단계에서부터 409가 나 fixture를 통과하지
    # 못한다. 여기서 실제로 검증하려는 건 signup() 자신의 재확인(동시 가입 레이스 대비)
    # 이므로, 사전 확인 단계를 건너뛰고 최종 엔드포인트를 직접 두드린다.
    res = client.post("/auth/signup", json={
        "email": "dup@test.com", "email_code": "000000",
        "phone": "010-0000-0000", "phone_code": "000000",
        "password": "pw12345!", "nickname": "중복", "marketing_agreed": False,
    })
    assert res.status_code == 409


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


def test_signup_verification_round_trips(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="model-test@example.com", purpose="email", code_hash="a" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    db_session.commit()

    found = db_session.query(SignupVerification).filter_by(
        target="model-test@example.com", purpose="email"
    ).one()
    assert found.attempts == 0
    assert len(found.code_hash) == 64


def test_signup_verification_unique_target_purpose(db_session):
    from datetime import datetime, timedelta, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="dupe@example.com", purpose="email", code_hash="a" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    db_session.commit()

    db_session.add(SignupVerification(
        target="dupe@example.com", purpose="email", code_hash="b" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_email_code_rejects_already_registered_email(client, seeded_user):
    res = client.post("/auth/signup/email-code", json={"email": "demo@dris.kr"})
    assert res.status_code == 409


def test_email_code_resend_cooldown(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)

    first = client.post("/auth/signup/email-code", json={"email": "cooldown@test.com"})
    assert first.status_code == 200
    second = client.post("/auth/signup/email-code", json={"email": "cooldown@test.com"})
    assert second.status_code == 429


def test_email_code_send_failure_returns_502(client, platforms, monkeypatch):
    from app.email_verification import EmailSendError
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")

    def _raise(to, code):
        raise EmailSendError("boom")

    monkeypatch.setattr(auth_router, "send_verification_email", _raise)

    res = client.post("/auth/signup/email-code", json={"email": "fail@test.com"})
    assert res.status_code == 502


def test_verify_email_code_wrong_code_rejected(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "wrongcode@test.com"})

    res = client.post("/auth/signup/verify-email-code", json={"email": "wrongcode@test.com", "code": "000000"})
    assert res.status_code == 400
    # 가벼운 사전 확인 엔드포인트는 어느 단계인지 프론트가 이미 알고 있으므로
    # label 없는 일반 메시지 그대로 (signup() 최종 제출과 구분).
    assert res.json()["detail"] == "인증번호가 올바르지 않습니다"


def test_verify_email_code_exceeds_max_attempts(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "toomany@test.com"})

    for _ in range(5):
        res = client.post("/auth/signup/verify-email-code", json={"email": "toomany@test.com", "code": "000000"})
        assert res.status_code == 400

    res = client.post("/auth/signup/verify-email-code", json={"email": "toomany@test.com", "code": "123456"})
    assert res.status_code == 400
    assert "초과" in res.json()["detail"]


def test_phone_code_returns_mock_code_directly(client, platforms):
    res = client.post("/auth/signup/phone-code", json={"phone": "010-1111-2222"})
    assert res.status_code == 200
    body = res.json()
    assert "mock_code" in body
    assert len(body["mock_code"]) == 6


def test_verify_phone_code_success(client, platforms):
    sent = client.post("/auth/signup/phone-code", json={"phone": "010-3333-4444"}).json()
    res = client.post("/auth/signup/verify-phone-code", json={"phone": "010-3333-4444", "code": sent["mock_code"]})
    assert res.status_code == 200


def test_signup_rejects_wrong_email_code(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "badflow@test.com"})
    client.post("/auth/signup/phone-code", json={"phone": "010-5555-1111"})

    res = client.post("/auth/signup", json={
        "email": "badflow@test.com", "email_code": "000000",
        "phone": "010-5555-1111", "phone_code": "123456",
        "password": "pw12345!", "nickname": "실패", "marketing_agreed": False,
    })
    assert res.status_code == 400
    # 최종 제출에서는 프론트가 이메일 단계로 되돌릴 수 있도록 "이메일 인증번호"로
    # 시작하는 구분 가능한 메시지를 받아야 한다 (frontend/src/app/signup/page.tsx의
    # submit() catch가 이 문자열을 substring-match한다).
    assert res.json()["detail"].startswith("이메일 인증번호")


def test_signup_rejects_wrong_phone_code(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "badflow2@test.com"})
    client.post("/auth/signup/phone-code", json={"phone": "010-5555-2222"})

    res = client.post("/auth/signup", json={
        "email": "badflow2@test.com", "email_code": "123456",
        "phone": "010-5555-2222", "phone_code": "000000",
        "password": "pw12345!", "nickname": "실패", "marketing_agreed": False,
    })
    assert res.status_code == 400
    assert res.json()["detail"].startswith("휴대폰 인증번호")


def test_signup_final_submit_expired_email_code_identifies_email_step(client, platforms, db_session, monkeypatch):
    """설계 문서 "최종 제출 시점에 코드 만료" 요구사항: 오래 붙잡고 있다가 제출하면
    어느 코드가 만료됐는지 메시지로 구분할 수 있어야 프론트가 해당 단계로 되돌릴 수 있다."""
    from datetime import datetime, timedelta, timezone

    from app.models import SignupVerification
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "expiredemail@test.com"})
    client.post("/auth/signup/phone-code", json={"phone": "010-6666-7777"})

    # 이메일 코드만 만료시킨다 (휴대폰 코드는 아직 유효).
    row = db_session.query(SignupVerification).filter_by(
        target="expiredemail@test.com", purpose="email"
    ).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    res = client.post("/auth/signup", json={
        "email": "expiredemail@test.com", "email_code": "123456",
        "phone": "010-6666-7777", "phone_code": "123456",
        "password": "pw12345!", "nickname": "만료테스트", "marketing_agreed": False,
    })
    assert res.status_code == 400
    assert res.json()["detail"].startswith("이메일 인증번호")
    assert "만료" in res.json()["detail"]


def test_phone_code_rejects_invalid_format(client, platforms):
    res = client.post("/auth/signup/phone-code", json={"phone": "not-a-phone"})
    assert res.status_code == 422


def test_issue_code_sweeps_expired_rows_for_same_purpose(client, platforms, db_session, monkeypatch):
    """형식 검증 없이도 phone-code는 서로 다른 target으로 스팸성 요청을 반복하면 만료된
    행이 무한정 쌓일 수 있다 — 코드 발급마다 같은 purpose의 만료 행을 쓸어내는지 확인."""
    from datetime import datetime, timedelta, timezone

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="stale-hash-1", purpose="phone", code_hash="a" * 64,
        expires_at=now - timedelta(minutes=1), attempts=0, created_at=now - timedelta(minutes=20),
    ))
    db_session.add(SignupVerification(
        target="stale-hash-2", purpose="phone", code_hash="b" * 64,
        expires_at=now - timedelta(minutes=1), attempts=0, created_at=now - timedelta(minutes=20),
    ))
    db_session.commit()

    res = client.post("/auth/signup/phone-code", json={"phone": "010-8888-9999"})
    assert res.status_code == 200

    remaining_stale = db_session.query(SignupVerification).filter(
        SignupVerification.target.in_(["stale-hash-1", "stale-hash-2"])
    ).count()
    assert remaining_stale == 0


def test_signup_consumes_verification_rows_on_success(client, platforms, db_session, signup_flow):
    from app.models import SignupVerification

    signup_flow("consumed@test.com", phone="010-7777-8888")
    remaining = db_session.query(SignupVerification).filter(
        SignupVerification.target.in_(["consumed@test.com"])
    ).count()
    assert remaining == 0
