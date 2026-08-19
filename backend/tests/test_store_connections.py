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
        shops = [(99999001, "테스트가게")]
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

    # 첫 리뷰 동기화가 끝나기 전에도 로그인 직후 브랜드 목록이 즉시 채워져야 한다.
    shops = client.get("/store-connections/baemin/shops", headers=auth_headers).json()
    assert shops == [{"shop_no": "99999001", "shop_name": "테스트가게"}]


def test_baemin_login_persists_all_discovered_shop_brands_immediately(client, seeded_user, platforms, auth_headers, monkeypatch):
    """한 로그인에 브랜드가 여러 개 딸린 계정(실 계정 4개 브랜드 케이스)도,
    리뷰 동기화를 한 번도 돌리지 않은 채 로그인 직후 전부 조회 가능해야 한다."""
    from cryptography.fernet import Fernet

    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    class _FakeMultiShopSession:
        shop_no = 11111
        shop_name = "브랜드A"
        shops = [(11111, "브랜드A"), (22222, "브랜드B"), (33333, "브랜드C")]
        closed = False

        def close(self):
            self.closed = True

    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(sc, "baemin_login", lambda login_id, password: fake_session)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "test_pw_123"},
        headers=auth_headers,
    )
    assert res.status_code == 200

    shops = client.get("/store-connections/baemin/shops", headers=auth_headers).json()
    assert shops == [
        {"shop_no": "11111", "shop_name": "브랜드A"},
        {"shop_no": "22222", "shop_name": "브랜드B"},
        {"shop_no": "33333", "shop_name": "브랜드C"},
    ]


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


def test_sync_reviews_rejected_while_job_already_in_progress(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.add(ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="running",
        started_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 409
    assert "이미 진행 중" in res.json()["detail"]


def test_sync_reviews_allowed_after_previous_job_finished(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection
    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.add(ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="success",
        started_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: None)

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 202


def test_list_baemin_shop_brands_returns_empty_when_no_brands_synced_yet(client, seeded_user, platforms, auth_headers):
    # seeded_user는 baemin 연결은 있지만(Mock) baemin_shop_brands 행은 없다
    res = client.get("/store-connections/baemin/shops", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == []


def test_list_baemin_shop_brands_returns_empty_when_no_baemin_connection_at_all(client, db_session, seeded_user, platforms, auth_headers):
    """seeded_user의 baemin 연결(Mock) 자체를 지워서, 연결이 아예 없는 경우의
    조기 반환 분기(conn is None)를 실제로 타는지 확인한다."""
    from app.models import StorePlatformConnection

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    db_session.delete(conn)
    db_session.commit()

    res = client.get("/store-connections/baemin/shops", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == []


def test_list_baemin_shop_brands_returns_rows_in_shop_no_order(client, db_session, seeded_user, platforms, auth_headers):
    from app.models import BaeminShopBrand, StorePlatformConnection

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    db_session.add_all([
        BaeminShopBrand(connection_id=conn.id, shop_no="22222", shop_name="브랜드B"),
        BaeminShopBrand(connection_id=conn.id, shop_no="11111", shop_name="브랜드A"),
    ])
    db_session.commit()

    res = client.get("/store-connections/baemin/shops", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body == [
        {"shop_no": "11111", "shop_name": "브랜드A"},
        {"shop_no": "22222", "shop_name": "브랜드B"},
    ]


def test_list_baemin_shop_brands_scoped_to_own_connection(client, db_session, seeded_user, platforms, auth_headers):
    """다른 사장의 연결에 딸린 브랜드가 내 브랜드 목록에 섞여 나오면 안 된다."""
    from datetime import datetime, timezone

    from app.auth import hash_password
    from app.models import BaeminShopBrand, Store, StorePlatformConnection, User

    other = User(email="brand-rival@test.com", password_hash=hash_password("x"), nickname="브랜드경쟁", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="라이벌가게3", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_conn = StorePlatformConnection(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-OTHER3", connected_at=datetime.now(timezone.utc),
    )
    db_session.add(other_conn)
    db_session.flush()
    db_session.add(BaeminShopBrand(connection_id=other_conn.id, shop_no="99999", shop_name="남의브랜드"))

    my_conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    db_session.add(BaeminShopBrand(connection_id=my_conn.id, shop_no="11111", shop_name="내브랜드"))
    db_session.commit()

    res = client.get("/store-connections/baemin/shops", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body == [{"shop_no": "11111", "shop_name": "내브랜드"}]
    assert "남의브랜드" not in [b["shop_name"] for b in body]


def test_baemin_login_delegates_to_worker_when_crawl_worker_url_set(client, seeded_user, platforms, auth_headers, monkeypatch):
    """Railway처럼 클라우드 IP에서 배민 로그인을 직접 실행하면 봇 탐지로
    로그인 폼 자체가 안 뜨는 현상이 실측 확인됐다(ads.py의 apply-bid와 동일한
    원인, 2026-08-18) — CRAWL_WORKER_URL이 설정돼 있으면 /internal/baemin-login로
    전체를 위임해야 한다. baemin_login을 호출되면 실패하는 스파이로 바꿔 이
    프로세스에서 절대 실행되지 않음을 증명한다."""
    from unittest.mock import Mock

    from cryptography.fernet import Fernet

    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(sc, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "test-secret")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("이 프로세스에서 직접 로그인을 시도하면 안 된다")

    monkeypatch.setattr(sc, "baemin_login", _fail_if_called)

    fake_response = Mock(
        status_code=200,
        json=Mock(return_value={
            "shop_no": 99999001, "shop_name": "테스트가게",
            "shops": [{"shop_no": 99999001, "shop_name": "테스트가게"}],
        }),
    )
    mock_post = Mock(return_value=fake_response)
    monkeypatch.setattr(sc.httpx, "post", mock_post)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "test_pw_123"},
        headers=auth_headers,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["shop_name"] == "테스트가게"
    assert body["platform_store_id"] == "99999001"
    mock_post.assert_called_once_with(
        "http://worker.example.com/internal/baemin-login",
        json={"login_id": "test_id", "password": "test_pw_123"},
        headers={"X-Worker-Secret": "test-secret"},
        timeout=60,
    )

    listed = client.get("/store-connections", headers=auth_headers).json()
    baemin_conn = next(c for c in listed if c["platform_code"] == "baemin")
    assert baemin_conn["has_real_credential"] is True  # 위임 경로에서도 암호화·저장은 Railway가 그대로 수행


def test_baemin_login_reports_worker_login_failure_as_400(client, seeded_user, platforms, auth_headers, monkeypatch):
    from unittest.mock import Mock

    from app.routers import store_connections as sc

    monkeypatch.setattr(sc, "_CRAWL_WORKER_URL", "http://worker.example.com")
    fake_response = Mock(
        status_code=400,
        headers={"content-type": "application/json"},
        json=Mock(return_value={"detail": "아이디 또는 비밀번호가 일치하지 않습니다"}),
    )
    monkeypatch.setattr(sc.httpx, "post", Mock(return_value=fake_response))

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "wrong"},
        headers=auth_headers,
    )

    assert res.status_code == 400
    assert "일치하지 않습니다" in res.json()["detail"]


def test_internal_baemin_login_rejects_wrong_worker_secret(client, monkeypatch):
    from app.routers import store_connections as sc

    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "correct-secret")

    res = client.post(
        "/internal/baemin-login",
        json={"login_id": "test_id", "password": "test_pw"},
        headers={"X-Worker-Secret": "wrong-secret"},
    )

    assert res.status_code == 403


def test_internal_baemin_login_returns_shop_list_on_success(client, monkeypatch):
    from app.routers import store_connections as sc

    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "correct-secret")

    class _FakeSession:
        shop_no = 12345
        shop_name = "치밥대장"
        shops = [(12345, "치밥대장"), (67890, "곱도리탕")]
        closed = False

        def close(self):
            self.closed = True

    fake_session = _FakeSession()
    monkeypatch.setattr(sc, "baemin_login", lambda login_id, password: fake_session)

    res = client.post(
        "/internal/baemin-login",
        json={"login_id": "test_id", "password": "test_pw"},
        headers={"X-Worker-Secret": "correct-secret"},
    )

    assert res.status_code == 200
    assert res.json() == {
        "shop_no": 12345, "shop_name": "치밥대장",
        "shops": [{"shop_no": 12345, "shop_name": "치밥대장"}, {"shop_no": 67890, "shop_name": "곱도리탕"}],
    }
    assert fake_session.closed is True


def test_sync_reviews_delegates_to_worker_when_crawl_worker_url_set(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from unittest.mock import Mock

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

    monkeypatch.setattr(sc, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "test-secret")

    def _fail_if_called(job_id):
        raise AssertionError("이 프로세스에서 직접 동기화를 실행하면 안 된다")

    monkeypatch.setattr(sc, "run_review_sync_job", _fail_if_called)

    fake_response = Mock(status_code=200)
    mock_post = Mock(return_value=fake_response)
    monkeypatch.setattr(sc.httpx, "post", mock_post)

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)

    assert res.status_code == 202
    job_id = res.json()["job_id"]
    mock_post.assert_called_once_with(
        "http://worker.example.com/internal/sync-reviews",
        params={"job_id": job_id},
        headers={"X-Worker-Secret": "test-secret"},
        timeout=15,
    )
    status = client.get(f"/store-connections/baemin/sync-status/{job_id}", headers=auth_headers).json()
    assert status["status"] == "pending"  # 위임 성공 시 job 상태는 워커가 이어서 갱신한다


def test_sync_reviews_marks_job_failed_when_worker_unreachable(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
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

    monkeypatch.setattr(sc, "_CRAWL_WORKER_URL", "http://worker.example.com")

    def _raise(*args, **kwargs):
        raise sc.httpx.RequestError("연결 실패")

    monkeypatch.setattr(sc.httpx, "post", _raise)

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)

    assert res.status_code == 202  # job은 만들어졌으니 job_id는 정상 반환
    job_id = res.json()["job_id"]
    status = client.get(f"/store-connections/baemin/sync-status/{job_id}", headers=auth_headers).json()
    assert status["status"] == "failed"
    assert "크롤 워커에 연결할 수 없습니다" in status["error_message"]


def test_internal_sync_reviews_rejects_wrong_worker_secret(client, monkeypatch):
    from app.routers import store_connections as sc

    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "correct-secret")

    res = client.post(
        "/internal/sync-reviews?job_id=1",
        headers={"X-Worker-Secret": "wrong-secret"},
    )

    assert res.status_code == 403


def test_internal_sync_reviews_starts_background_job(client, monkeypatch):
    from app.routers import store_connections as sc

    monkeypatch.setattr(sc, "_CRAWL_WORKER_SECRET", "correct-secret")
    called = {}
    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: called.setdefault("job_id", job_id))

    res = client.post(
        "/internal/sync-reviews?job_id=42",
        headers={"X-Worker-Secret": "correct-secret"},
    )

    assert res.status_code == 200
    assert res.json() == {"status": "started"}
    assert called == {"job_id": 42}


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


def test_sync_reviews_manual_dispatch_records_triggered_by_manual(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection
    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: None)

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    job = db_session.query(ReviewSyncJob).filter_by(id=job_id).one()
    assert job.triggered_by == "manual"
