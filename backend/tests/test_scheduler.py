import asyncio
from datetime import datetime, timezone

from app.scheduler import KST, run_scheduled_sync_cycle, seconds_until_next_run


def test_seconds_until_next_run_before_4am_same_day():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 3 * 3600


def test_seconds_until_next_run_after_4am_same_day():
    now = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 18 * 3600


def test_seconds_until_next_run_exactly_4am_rolls_to_next_day():
    now = datetime(2026, 8, 20, 4, 0, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 24 * 3600


def test_seconds_until_next_run_converts_utc_input_to_kst():
    # 2026-08-19 19:00 UTC == 2026-08-20 04:00 KST(UTC+9) — 정확히 목표 시각이라 다음 날로 넘어가야 한다
    now = datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc)
    assert seconds_until_next_run(now) == 24 * 3600


def test_run_scheduled_sync_cycle_skips_connections_without_real_credential(db_session, seeded_user, platforms, monkeypatch):
    import app.scheduler as scheduler_mod
    from app.models import ReviewSyncJob

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert calls == []
    assert db_session.query(ReviewSyncJob).count() == 0


def test_run_scheduled_sync_cycle_dispatches_job_for_real_credential_connection(db_session, seeded_user, platforms, monkeypatch):
    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    job = db_session.query(ReviewSyncJob).filter_by(store_id=seeded_user["store"].id).one()
    assert job.triggered_by == "scheduled"
    assert calls == [job.id]


def test_run_scheduled_sync_cycle_skips_store_with_job_already_in_progress(db_session, seeded_user, platforms, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.add(ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="running",
        started_at=datetime.now(timezone.utc), triggered_by="manual",
    ))
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert calls == []
    assert db_session.query(ReviewSyncJob).filter_by(store_id=seeded_user["store"].id).count() == 1


def test_run_scheduled_sync_cycle_does_not_double_dispatch_when_worker_url_set(db_session, seeded_user, platforms, monkeypatch):
    from unittest.mock import Mock

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_SECRET", "test-secret")
    fake_response = Mock(status_code=200)
    fake_post = Mock(return_value=fake_response)
    monkeypatch.setattr(scheduler_mod.store_connections.httpx, "post", fake_post)

    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    fake_post.assert_called_once()  # 워커 위임 경로가 실제로 탔는지 확인(우연히 스킵되면 안 됨)
    assert calls == []  # 워커에 위임됐으니 이 프로세스에서 또 돌리면 이중 실행


def test_run_scheduled_sync_cycle_continues_after_one_store_raises(db_session, seeded_user, platforms, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.auth import hash_password
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, Store, StorePlatformConnection, User

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn1 = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn1.credential_ciphertext = encrypt_credential("id1", "pw1")

    user2 = User(
        email="demo2@dris.kr", password_hash=hash_password("demo1234!"), nickname="박사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user2)
    db_session.flush()
    store2 = Store(user_id=user2.id, name="족발대장", category="족발", created_at=datetime.now(timezone.utc))
    db_session.add(store2)
    db_session.flush()
    db_session.add(StorePlatformConnection(
        store_id=store2.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-2", business_number="000-00-00002",
        connected_at=datetime.now(timezone.utc),
        credential_ciphertext=encrypt_credential("id2", "pw2"),
    ))
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)

    calls = []

    def _fake_run(job_id):
        job = db_session.get(ReviewSyncJob, job_id)
        if job.store_id == seeded_user["store"].id:
            raise RuntimeError("boom")
        calls.append(job_id)

    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", _fake_run)

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert len(calls) == 1  # 다른 매장은 정상 처리됨
    assert db_session.query(ReviewSyncJob).count() == 2  # 두 매장 다 잡은 생성됨(실행 성패와 무관)


def test_run_scheduled_sync_cycle_rolls_back_after_db_commit_failure_so_other_stores_still_sync(
    db_session, seeded_user, platforms, monkeypatch,
):
    """_dispatch_sync_job의 db.commit()이 실패해도(제약 위반, 커넥션 끊김 등)
    세션이 롤백되지 않으면 SQLAlchemy 세션이 PendingRollbackError 상태로
    남아 이후 모든 매장이 연쇄로 실패한다. run_scheduled_sync_cycle의
    except 블록이 db.rollback()을 호출해 다음 매장이 영향받지 않는지
    검증한다."""
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.auth import hash_password
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, Store, StorePlatformConnection, User

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn1 = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn1.credential_ciphertext = encrypt_credential("id1", "pw1")

    user2 = User(
        email="demo3@dris.kr", password_hash=hash_password("demo1234!"), nickname="이사장",
        phone_hash="c" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user2)
    db_session.flush()
    store2 = Store(user_id=user2.id, name="분식대장", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(store2)
    db_session.flush()
    db_session.add(StorePlatformConnection(
        store_id=store2.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-3", business_number="000-00-00003",
        connected_at=datetime.now(timezone.utc),
        credential_ciphertext=encrypt_credential("id3", "pw3"),
    ))
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: None)

    original_commit = db_session.commit
    call_count = {"n": 0}

    def _flaky_commit():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("db commit boom")  # 첫 매장(store1) 처리 중 DB 커밋 실패를 흉내
        return original_commit()

    monkeypatch.setattr(db_session, "commit", _flaky_commit)

    asyncio.run(run_scheduled_sync_cycle(db_session))

    # 첫 매장(store1)의 커밋 실패는 롤백됐으므로 잡이 생성되지 않는다
    assert db_session.query(ReviewSyncJob).filter_by(store_id=seeded_user["store"].id).count() == 0
    # 두 번째 매장(store2)은 첫 매장의 실패/롤백에 영향받지 않고 정상적으로 잡이 생성된다
    job2 = db_session.query(ReviewSyncJob).filter_by(store_id=store2.id).one()
    assert job2.triggered_by == "scheduled"
