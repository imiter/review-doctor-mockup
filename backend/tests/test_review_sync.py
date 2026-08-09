from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import CredentialCryptoError, encrypt_credential
from app.models import BaeminShopBrand, Review, ReviewSyncJob, StorePlatformConnection
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
    shop_name = "테스트매장"
    shops = [(99999001, "테스트매장")]
    page = object()
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
        lambda page, shop_no: [_RAW_1, _RAW_2, _RAW_HIDDEN],
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


def test_sync_records_mapping_failure_on_missing_field_and_still_closes_session(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    _raw_missing_nickname = {
        "id": 2001, "rating": 5.0, "contents": "닉네임 필드 누락",
        "orderCount": 1, "menus": [{"name": "메뉴"}], "createdAt": "2026-08-04T10:00:00+09:00",
        "displayStatus": "DISPLAY",
        # memberNickname 누락 — 배민 비공식 API 응답이 변경/축소된 상황을 흉내낸다
    }
    monkeypatch.setattr(
        review_sync_mod, "fetch_all_reviews",
        lambda page, shop_no: [_raw_missing_nickname],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"  # KeyError로 running에 멈추지 않고 failed로 기록돼야 함
    assert job.error_message is not None
    assert job.finished_at is not None
    assert fake_session.closed is True


def test_sync_records_fetch_failure_and_still_closes_session(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise(page, shop_no):
        raise BaeminScrapeError("리뷰 조회 실패: HTTP 500")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert "HTTP 500" in job.error_message
    assert fake_session.closed is True


def test_sync_records_credential_decryption_failure(db_session, sync_setup, monkeypatch):
    """CREDENTIAL_ENCRYPTION_KEY가 바뀌는 등으로 복호화가 실패해도 running에 멈추지 않아야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup

    def _raise(ciphertext):
        raise CredentialCryptoError("test message")

    monkeypatch.setattr(review_sync_mod, "decrypt_credential", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert job.error_message is not None
    assert "test message" in job.error_message
    assert job.finished_at is not None


def test_sync_dedupes_duplicate_external_id_within_same_batch(db_session, sync_setup, monkeypatch):
    """fetch_all_reviews가 페이지네이션 겹침 등으로 같은 external_review_id를
    한 배치 안에서 두 번 반환해도, 두 번째는 조용히 스킵돼야 한다(IntegrityError로
    번지지 않고 job이 success로 종결돼야 함)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    _raw_dup = {**_RAW_2}  # id=1002, 배치 내에서 두 번 등장하는 상황을 흉내낸다
    monkeypatch.setattr(
        review_sync_mod, "fetch_all_reviews",
        lambda page, shop_no: [_raw_dup, dict(_raw_dup)],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 2
    assert job.reviews_inserted == 1  # 배치 내 중복은 두 번째부터 스킵
    assert fake_session.closed is True

    rows = db_session.query(Review).filter_by(external_review_id=1002).all()
    assert len(rows) == 1


def test_sync_succeeds_with_zero_reviews_when_fetch_returns_empty_list(db_session, sync_setup, monkeypatch):
    """fetch_all_reviews가 (엔드포인트를 관측했지만 리뷰가 없어서) 빈 리스트를
    반환하는 정상 케이스는 여전히 success/reviews_fetched=0으로 끝나야 한다 —
    이는 BaeminScrapeError를 던지는 "한 번도 못 봄" 실패 경로(다른 테스트에서
    검증)와는 구분되는, 명시적으로 유효한 상태다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 0
    assert job.reviews_inserted == 0
    assert fake_session.closed is True


def test_sync_records_unclassified_fetch_exception_and_still_closes_session(db_session, sync_setup, monkeypatch):
    """fetch_all_reviews가 BaeminScrapeError/KeyError가 아닌 예외(예: Playwright 내부 오류)를
    던져도 안전망이 job을 failed로 종결시키고 세션도 닫아야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise(page, shop_no):
        raise RuntimeError("simulated Playwright error")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert job.error_message is not None
    assert job.finished_at is not None
    assert fake_session.closed is True


_RAW_SHOP_A = {
    "id": 3001, "rating": 5.0, "contents": "매장A 리뷰", "memberNickname": "고객A",
    "orderCount": 1, "menus": [{"name": "메뉴A"}], "createdAt": "2026-08-05T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}
_RAW_SHOP_B = {
    "id": 3002, "rating": 4.0, "contents": "매장B 리뷰", "memberNickname": "고객B",
    "orderCount": 1, "menus": [{"name": "메뉴B"}], "createdAt": "2026-08-06T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}


class _FakeMultiShopSession:
    shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    page = object()
    closed = False

    def close(self):
        self.closed = True


def test_sync_syncs_all_shops_and_sums_counts_with_distinct_shop_tags(db_session, sync_setup, monkeypatch):
    """계정에 브랜드가 2개면 두 매장 모두 동기화되고, 각 리뷰는 자신이 온
    매장의 platform_shop_no로 태깅돼야 한다. baemin_shop_brands도 두 매장
    모두 저장돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _fetch(page, shop_no):
        return [_RAW_SHOP_A] if shop_no == 11111 else [_RAW_SHOP_B]

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _fetch)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 2
    assert job.reviews_inserted == 2
    assert job.error_message is None  # 실패한 매장이 없는 흔한 경우엔 노이즈를 남기지 않는다
    assert fake_session.closed is True

    review_a = db_session.query(Review).filter_by(external_review_id=3001).one()
    review_b = db_session.query(Review).filter_by(external_review_id=3002).one()
    assert review_a.platform_shop_no == "11111"
    assert review_b.platform_shop_no == "22222"

    brands = {
        b.shop_no: b.shop_name
        for b in db_session.query(BaeminShopBrand).filter_by(connection_id=conn.id).all()
    }
    assert brands == {"11111": "브랜드A", "22222": "브랜드B"}


def test_sync_partial_shop_failure_still_succeeds_with_only_successful_shop_counted(db_session, sync_setup, monkeypatch):
    """4개 브랜드 중 일부만 실패하는 현실적인 상황을 2개 매장으로 축소해 흉내낸다
    — 한 매장이 BaeminScrapeError를 던져도 job은 실패가 아니라 성공으로
    끝나야 하고, 카운트/삽입은 성공한 매장분만 반영돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _fetch(page, shop_no):
        if shop_no == 11111:
            raise BaeminScrapeError("일시적 오류")
        return [_RAW_SHOP_B]

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _fetch)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 1
    assert job.reviews_inserted == 1
    assert fake_session.closed is True

    # 부분 실패는 조용히 묻히면 안 된다 — job은 success지만 몇 개 중 몇 개가
    # 실패했는지가 error_message에 남아야 한다(이게 없으면 sync-status
    # 엔드포인트가 매번 "깨끗한 성공"만 보여주게 된다).
    assert job.error_message is not None
    assert "2개 중 1개 매장 동기화 실패" in job.error_message
    assert "브랜드A" in job.error_message
    assert "일시적 오류" in job.error_message

    assert db_session.query(Review).filter_by(external_review_id=3002).one()
    assert db_session.query(Review).filter_by(external_review_id=3001).first() is None

    # 실패한 매장도 로그인 단계에서 이름은 이미 확인됐으므로 브랜드 자체는 저장된다.
    brands = {b.shop_no for b in db_session.query(BaeminShopBrand).filter_by(connection_id=conn.id).all()}
    assert brands == {"11111", "22222"}


def test_sync_all_shops_failing_marks_job_failed(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise(page, shop_no):
        raise BaeminScrapeError(f"매장 {shop_no} 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert job.error_message is not None
    assert "22222" in job.error_message  # 마지막으로 시도한 매장의 에러가 남는다
    assert fake_session.closed is True
    assert db_session.query(Review).count() == 0


def test_sync_upserts_shop_brand_name_change_without_duplicate(db_session, sync_setup, monkeypatch):
    """이전 동기화에서 저장된 브랜드명이 배민 쪽에서 바뀐 경우, 재동기화 시
    기존 baemin_shop_brands 행을 갱신해야지 중복 행을 만들면 안 된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup

    db_session.add(BaeminShopBrand(connection_id=conn.id, shop_no="11111", shop_name="옛날이름"))
    db_session.commit()

    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    rows = db_session.query(BaeminShopBrand).filter_by(connection_id=conn.id, shop_no="11111").all()
    assert len(rows) == 1
    assert rows[0].shop_name == "브랜드A"  # 갱신됨, 중복 생성 안 됨
