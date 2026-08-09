def test_list_connections_includes_seed_baemin(client, seeded_user, auth_headers):
    res = client.get("/store-connections", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["platform_code"] == "baemin"
    assert body[0]["platform_store_id"] == "MK-1"
    assert body[0]["has_real_credential"] is False


def test_connect_new_platform_creates_mock_ids(client, seeded_user, platforms, auth_headers):
    res = client.post("/store-connections", json={"platform_id": platforms["yogiyo"].id}, headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["platform_code"] == "yogiyo"
    assert body["platform_store_id"].startswith("MK-")
    assert body["business_number"]

    listed = client.get("/store-connections", headers=auth_headers).json()
    assert len(listed) == 2


def test_connect_duplicate_platform_rejected(client, seeded_user, platforms, auth_headers):
    res = client.post("/store-connections", json={"platform_id": platforms["baemin"].id}, headers=auth_headers)
    assert res.status_code == 409


def test_disconnect_platform(client, seeded_user, platforms, auth_headers):
    connected = client.post("/store-connections", json={"platform_id": platforms["yogiyo"].id}, headers=auth_headers).json()

    res = client.delete(f"/store-connections/{connected['id']}", headers=auth_headers)
    assert res.status_code == 204

    listed = client.get("/store-connections", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["platform_code"] == "baemin"


def test_disconnect_other_users_connection_forbidden(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timezone

    from app.auth import hash_password
    from app.models import Store, StorePlatformConnection, User

    other = User(email="rival@test.com", password_hash=hash_password("x"), nickname="경쟁사장", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="라이벌가게", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_conn = StorePlatformConnection(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-OTHER", business_number="000", connected_at=datetime.now(timezone.utc),
    )
    db_session.add(other_conn)
    db_session.commit()

    res = client.delete(f"/store-connections/{other_conn.id}", headers=auth_headers)
    assert res.status_code == 404


def test_baemin_login_upgrades_existing_mock_connection(client, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    class _FakeSession:
        shop_no = 99999001
        shop_name = "테스트가게"
        closed = False

        def close(self):
            self.closed = True

    fake_session = _FakeSession()
    monkeypatch.setattr(sc, "baemin_login", lambda login_id, password: fake_session)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "test_pw_123"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["shop_name"] == "테스트가게"
    assert body["platform_store_id"] == "99999001"
    assert fake_session.closed is True

    listed = client.get("/store-connections", headers=auth_headers).json()
    baemin_conn = next(c for c in listed if c["platform_code"] == "baemin")
    assert baemin_conn["platform_store_id"] == "99999001"  # 시드의 Mock MK-1이 실제 값으로 교체됨
    assert baemin_conn["has_real_credential"] is True
    assert len(listed) == 1  # 새로 만들지 않고 기존 연결을 업그레이드


def test_baemin_login_failure_returns_400_with_baemin_message(client, seeded_user, platforms, auth_headers, monkeypatch):
    from app.routers import store_connections as sc
    from scrapers.baemin_auth import BaeminLoginError

    def _raise(login_id, password):
        raise BaeminLoginError("아이디 또는 비밀번호가 일치하지 않습니다")

    monkeypatch.setattr(sc, "baemin_login", _raise)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "wrong"},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "일치하지 않습니다" in res.json()["detail"]


def test_sync_reviews_requires_baemin_login_first(client, seeded_user, platforms, auth_headers):
    # seeded_user의 baemin 연결은 Mock(credential_ciphertext 없음)이라 동기화 불가
    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 400


def test_sync_reviews_creates_pending_job_and_dispatches_background_task(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import StorePlatformConnection
    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    calls = []
    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: calls.append(job_id))

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    assert calls == [job_id]  # BackgroundTasks가 올바른 job_id로 호출됨(TestClient는 응답 전 동기 실행)

    status = client.get(f"/store-connections/baemin/sync-status/{job_id}", headers=auth_headers).json()
    assert status["id"] == job_id
    assert status["status"] == "pending"  # run_review_sync_job을 no-op으로 바꿨으니 상태 변경 없음


def test_sync_status_forbidden_for_other_users_job(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timezone

    from app.auth import hash_password
    from app.models import ReviewSyncJob, Store, User

    other = User(email="rival2@test.com", password_hash=hash_password("x"), nickname="경쟁사장2", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="라이벌가게2", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_job = ReviewSyncJob(
        store_id=other_store.id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(other_job)
    db_session.commit()

    res = client.get(f"/store-connections/baemin/sync-status/{other_job.id}", headers=auth_headers)
    assert res.status_code == 404
