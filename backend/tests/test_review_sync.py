from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import encrypt_credential
from app.models import Review, ReviewSyncJob, StorePlatformConnection
from app.review_sync import sync_reviews_for_job
from scrapers.baemin_auth import BaeminLoginError
from scrapers.baemin_reviews import BaeminScrapeError

_RAW_1 = {
    "id": 1001, "rating": 5.0, "contents": "이미 있는 리뷰", "memberNickname": "기존고객",
    "orderCount": 1, "menus": [{"name": "기존메뉴"}], "createdAt": "2026-08-01T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}
_RAW_2 = {
    "id": 1002, "rating": 4.0, "contents": "새 리뷰입니다", "memberNickname": "새고객",
    "orderCount": 2, "menus": [{"name": "새메뉴"}], "createdAt": "2026-08-02T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}
_RAW_HIDDEN = {
    "id": 1003, "rating": 1.0, "contents": "숨김 리뷰", "memberNickname": "숨김고객",
    "orderCount": 1, "menus": [{"name": "메뉴"}], "createdAt": "2026-08-03T10:00:00+09:00",
    "displayStatus": "HIDDEN",
}


class _FakeSession:
    shop_no = 99999001
    request_context = object()
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture()
def sync_setup(db_session, seeded_user, platforms, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        status="pending", started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    return job, conn


def test_sync_inserts_new_reviews_and_skips_duplicates_and_hidden(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="기존메뉴",
        external_review_id=1001, rating=5, content="이미 있는 리뷰", customer_nickname="기존고객",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(
        review_sync_mod, "fetch_all_reviews",
        lambda request_context, shop_no: [_RAW_1, _RAW_2, _RAW_HIDDEN],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 2  # HIDDEN 리뷰는 제외
    assert job.reviews_inserted == 1  # id=1001은 중복 스킵
    assert fake_session.closed is True

    inserted = db_session.query(Review).filter_by(external_review_id=1002).one()
    assert inserted.customer_nickname == "새고객"
    assert inserted.menu_summary == "새메뉴"


def test_sync_records_login_failure(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup

    def _raise(login_id, password):
        raise BaeminLoginError("아이디 또는 비밀번호가 일치하지 않습니다")

    monkeypatch.setattr(review_sync_mod, "baemin_login", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert "일치하지 않습니다" in job.error_message
    assert job.finished_at is not None


def test_sync_records_fetch_failure_and_still_closes_session(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise(request_context, shop_no):
        raise BaeminScrapeError("리뷰 조회 실패: HTTP 500")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert "HTTP 500" in job.error_message
    assert fake_session.closed is True
