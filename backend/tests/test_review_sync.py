from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import CredentialCryptoError, encrypt_credential
from app.models import AdCampaign, Alert, BaeminShopBrand, BrandAdClickMetric, BrandMenuInfo, DailySettlement, Order, RepurchaseMetric, Review, ReviewReply, ReviewSyncJob, StorePlatformConnection
from app.review_sync import sync_reviews_for_job, upsert_brand_ad_click_metric, upsert_daily_settlement, upsert_order, upsert_repurchase_metric
from scrapers.baemin_ads import BaeminAdsScrapeError
from scrapers.baemin_auth import BaeminLoginError
from scrapers.baemin_menu import BaeminMenuScrapeError
from scrapers.baemin_reviews import BaeminScrapeError
from scrapers.baemin_stats import BaeminStatsScrapeError

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
_RAW_ALREADY_REPLIED = {
    "id": 1004, "rating": 5.0, "contents": "이미 사장님이 답글 단 리뷰", "memberNickname": "답글받은고객",
    "orderCount": 1, "menus": [{"name": "메뉴"}], "createdAt": "2026-08-04T10:00:00+09:00",
    "displayStatus": "DISPLAY",
    "comments": [{
        # 실 계정에서 확인한 실제 형식: 답글의 createdAt은 타임존 오프셋이 없는
        # naive datetime 문자열이다(리뷰 자체의 createdAt과는 다르다).
        "id": 5001, "managerNickname": "사장님", "contents": "감사합니다! 또 방문해주세요.",
        "displayType": "CEO", "displayStatus": "DISPLAY", "createdAt": "2026-08-04T18:00:00.123456",
    }],
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
    import app.review_sync as review_sync_mod

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

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "fetch_orders", lambda page, start_date, end_date, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", lambda page, shop_no, months: [])
    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", lambda page, start_date, end_date, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_menu_info",
        lambda page, shop_no: {"store_intro": "", "food_origin": "", "menu_intro": "", "menu_items": []},
    )

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
        lambda page, shop_no, **kwargs: [_RAW_1, _RAW_2, _RAW_HIDDEN],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 2  # HIDDEN 리뷰는 제외
    assert job.reviews_inserted == 1  # id=1001은 중복 스킵
    assert fake_session.closed is True

    inserted = db_session.query(Review).filter_by(external_review_id=1002).one()
    assert inserted.customer_nickname == "새고객"
    assert inserted.menu_summary == "새메뉴"


def test_sync_captures_owner_reply_already_on_baemin_as_final_reply(db_session, sync_setup, monkeypatch):
    # 배민에 이미 사장님이 직접 단 답글이 있는 리뷰는 우리 DB에도 그 답글
    # 내용을 review_replies(final)로 같이 적재해야 하고, 리뷰 자체 상태도
    # "answered"여야 한다 — 안 그러면 이미 답한 리뷰를 앱이 "미답변"으로
    # 잘못 보여주게 된다.
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(
        review_sync_mod, "fetch_all_reviews",
        lambda page, shop_no, **kwargs: [_RAW_ALREADY_REPLIED],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_inserted == 1

    review = db_session.query(Review).filter_by(external_review_id=1004).one()
    assert review.status == "answered"

    reply = db_session.query(ReviewReply).filter_by(review_id=review.id).one()
    assert reply.reply_type == "final"
    assert reply.style_id is None
    assert reply.content == "감사합니다! 또 방문해주세요."
    assert reply.created_at == datetime.fromisoformat("2026-08-04T18:00:00.123456")


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
        lambda page, shop_no, **kwargs: [_raw_missing_nickname],
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

    def _raise(page, shop_no, **kwargs):
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
        lambda page, shop_no, **kwargs: [_raw_dup, dict(_raw_dup)],
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

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

    def _raise(page, shop_no, **kwargs):
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

    def _fetch(page, shop_no, **kwargs):
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

    def _fetch(page, shop_no, **kwargs):
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
    assert "2개 중 1개 매장 리뷰 동기화 실패" in job.error_message
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

    def _raise(page, shop_no, **kwargs):
        raise BaeminScrapeError(f"매장 {shop_no} 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert job.error_message is not None
    assert "22222" in job.error_message  # 마지막으로 시도한 매장의 에러가 남는다
    assert fake_session.closed is True
    assert db_session.query(Review).count() == 0


def test_run_review_sync_job_marks_job_failed_when_sync_raises_unexpectedly(db_session, sync_setup, monkeypatch):
    """run_review_sync_job은 sync_reviews_for_job이 던지는 예상 밖 예외(예: DB
    커넥션 자체가 죽어서 나는 오류)를 잡아 잡을 failed로 기록해야 한다 —
    안 그러면 그 잡이 pending에 영원히 갇혀서 이후 모든 동기화 시도(수동+
    스케줄러)가 "이미 진행 중"으로 막힌다(2026-08-30 실측 사고: 크롤 워커의
    DB 커넥션이 idle 중 죽어있어 run_review_sync_job의 첫 줄 db.get(...)에서
    바로 예외가 났는데 아무도 안 잡아서 잡이 계속 pending으로 남았다)."""
    import app.review_sync as review_sync_mod
    from sqlalchemy.orm import sessionmaker

    job, conn = sync_setup

    # run_review_sync_job은 자기만의 세션(SessionLocal())을 여는데, 테스트
    # DB(db_session)와 같은 SQLite 엔진에 바인딩된 세션메이커로 바꿔치기해야
    # 두 세션이 같은 테스트 DB를 보게 된다.
    test_sessionmaker = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    monkeypatch.setattr(review_sync_mod, "SessionLocal", test_sessionmaker)

    def _boom(job, conn, db):
        raise RuntimeError("커넥션이 끊겼습니다 (시뮬레이션)")

    monkeypatch.setattr(review_sync_mod, "sync_reviews_for_job", _boom)

    review_sync_mod.run_review_sync_job(job.id)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message is not None
    assert "커넥션이 끊겼습니다" in job.error_message
    assert job.finished_at is not None


def test_sync_upserts_shop_brand_name_change_without_duplicate(db_session, sync_setup, monkeypatch):
    """이전 동기화에서 저장된 브랜드명이 배민 쪽에서 바뀐 경우, 재동기화 시
    기존 baemin_shop_brands 행을 갱신해야지 중복 행을 만들면 안 된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup

    db_session.add(BaeminShopBrand(connection_id=conn.id, shop_no="11111", shop_name="옛날이름"))
    db_session.commit()

    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    rows = db_session.query(BaeminShopBrand).filter_by(connection_id=conn.id, shop_no="11111").all()
    assert len(rows) == 1
    assert rows[0].shop_name == "브랜드A"  # 갱신됨, 중복 생성 안 됨


def test_upsert_daily_settlement_creates_new_row(db_session, seeded_user, platforms):
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).one()
    assert row.sales_amount == 50000
    assert row.deposit_amount == 30000


def test_upsert_daily_settlement_updates_existing_mock_row_for_same_platform(db_session, seeded_user, platforms):
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        settle_date=date.fromisoformat("2026-08-10"), sales_amount=999, deposit_amount=888,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    rows = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).all()
    assert len(rows) == 1  # 중복 행이 아니라 갱신
    assert rows[0].sales_amount == 50000
    assert rows[0].deposit_amount == 30000


def test_upsert_daily_settlement_leaves_other_platform_rows_untouched(db_session, seeded_user, platforms):
    # 요기요 Mock 행은 배민 동기화와 무관하게 그대로 남아야 한다.
    yogiyo_row = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id,
        settle_date=date.fromisoformat("2026-08-10"), sales_amount=12345, deposit_amount=11111,
    )
    db_session.add(yogiyo_row)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    untouched = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id, settle_date="2026-08-10",
    ).one()
    assert untouched.sales_amount == 12345
    assert untouched.deposit_amount == 11111


def test_upsert_daily_settlement_only_sales_leaves_deposit_untouched_on_existing_row(db_session, seeded_user, platforms):
    # 매출만 갱신하고 입금은 건드리지 않아야 하는 경우(예: 정산 API가 실패해도
    # 매출은 저장 가능해야 하는 부분 성공 시나리오)를 대비한다.
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        settle_date=date.fromisoformat("2026-08-10"), sales_amount=999, deposit_amount=777,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).one()
    assert row.sales_amount == 50000
    assert row.deposit_amount == 777  # 안 건드림


def test_upsert_repurchase_metric_creates_new_row(db_session, seeded_user, platforms):
    upsert_repurchase_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        new_orders=3, repeat_orders=2, rate_raw=0.4, rate_adjusted=0.35,
    )
    db_session.commit()

    row = db_session.query(RepurchaseMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, metric_date="2026-08-10",
    ).one()
    assert row.new_orders == 3
    assert row.repeat_orders == 2
    assert float(row.rate_raw) == 0.4
    assert float(row.rate_adjusted) == 0.35


def test_upsert_repurchase_metric_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    existing = RepurchaseMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        metric_date=date.fromisoformat("2026-08-10"),
        new_orders=1, repeat_orders=1, rate_raw="0.5", rate_adjusted="0.5",
    )
    db_session.add(existing)
    db_session.commit()

    upsert_repurchase_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        new_orders=3, repeat_orders=2, rate_raw=0.4, rate_adjusted=0.35,
    )
    db_session.commit()

    rows = db_session.query(RepurchaseMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, metric_date="2026-08-10",
    ).all()
    assert len(rows) == 1
    assert rows[0].new_orders == 3


_SALES_RESP = {"graph": {"data": [{"x": "2026-08-10", "y": 50000.0}]}, "orderAmount": 50000.0, "orderCount": 2}
_CRM_RESP = {
    "orderSummary": {"orderCount": 2, "orderPrice": 50000.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 1,
        "timeNewGraph": {"data": [{"x": "2026-08-10", "y": 1}]},
        "timeReorderGraph": {"data": [{"x": "2026-08-10", "y": 1}]},
    },
}
_SETTLE_RESP = {
    "contents": [{"giveId": 900001, "depositDueDate": "2026-08-10", "giveAmount": 40000, "giveStatus": "REQUEST"}],
    "totalSize": 1,
}


def test_sync_upserts_sales_deposit_repurchase_when_all_succeed(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date, **kwargs: [_SETTLE_RESP],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000
    assert settlement.deposit_amount == 40000

    repurchase = db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one()
    assert repurchase.new_orders == 1
    assert repurchase.repeat_orders == 1


def test_sync_merges_current_month_orders_into_sales_for_a_different_date(db_session, sync_setup, monkeypatch):
    """가게통계(완료된 3개월)와 주문내역(이번 달 진행분)은 서로 다른 날짜를
    다루므로, 두 소스의 매출이 각자의 날짜에 정확히 반영돼야 한다 — 완료된
    달 데이터를 이번 달 데이터가 덮어쓰면 안 된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], []),  # 2026-08-10에 50000원 (완료된 달분)
    )
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    completed_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert completed_month_row.sales_amount == 50000  # 가게통계분, 안 건드려짐

    current_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-15",
    ).one()
    assert current_month_row.sales_amount == 12000  # 주문내역분


def test_sync_isolates_current_month_orders_failure_from_completed_months_sales(db_session, sync_setup, monkeypatch):
    """이번 달(주문내역) 보완 조회만 실패해도 완료된 3개월분 매출은 정상
    저장돼야 한다 — 항목별 독립 실패 격리 원칙이 이 소스에도 적용된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], []),
    )
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [])

    def _raise_current_month(page, start_date, end_date, **kwargs):
        raise BaeminStatsScrapeError("주문내역 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _raise_current_month)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    completed_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert completed_month_row.sales_amount == 50000
    assert "주문내역" in job.error_message


def test_sync_merges_current_month_sales_and_settlement_deposit_on_the_same_date(db_session, sync_setup, monkeypatch):
    """오늘 날짜는 항상 두 소스 모두의 대상이다 — 주문내역(이번 달 진행분
    매출)과 정산내역(입금)이 같은 settle_date를 각각 다른 호출로 upsert할 때,
    autoflush=False인 프로덕션 세션에서는 두 번째 호출이 첫 번째 호출의
    아직 flush 안 된 add()를 못 봐서 같은 키로 중복 INSERT를 시도해
    UniqueViolation이 났다(실 배민 계정으로 로컬 검증 중 실제로 재현됨).
    한 행에 매출과 입금이 함께 정확히 반영돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date, **kwargs: [
            {"contents": [{"giveId": 900002, "depositDueDate": "2026-08-15", "giveAmount": 9000, "giveStatus": "REQUEST"}], "totalSize": 1},
        ],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-15",
    ).one()
    assert row.sales_amount == 12000
    assert row.deposit_amount == 9000


def test_sync_sums_stats_across_multiple_shops(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    sales_a = {"graph": {"data": [{"x": "2026-08-10", "y": 30000.0}]}, "orderAmount": 30000.0, "orderCount": 1}
    sales_b = {"graph": {"data": [{"x": "2026-08-10", "y": 20000.0}]}, "orderAmount": 20000.0, "orderCount": 1}

    def _fetch_stats(page, shop_no, months):
        return ([sales_a], [_CRM_RESP]) if shop_no == 11111 else ([sales_b], [_CRM_RESP])

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [_SETTLE_RESP])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000  # 30000 + 20000


def test_sync_reports_success_with_error_message_when_stats_fail_but_reviews_succeed(db_session, sync_setup, monkeypatch):
    """리뷰는 성공했는데 매출/재주문율/입금 수집이 전부 실패해도 job 자체는
    success로 남고 error_message에 어떤 부분이 실패했는지 남아야 한다(설계
    문서 에러 처리 표 참고)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    def _raise_stats(page, shop_no, months):
        raise BaeminStatsScrapeError("매출 통계 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _raise_stats)

    def _raise_settlement(page, start_date, end_date, **kwargs):
        raise BaeminStatsScrapeError("정산내역 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _raise_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"  # 리뷰는 성공했으므로 전체 실패 아님
    assert job.reviews_inserted == 1
    assert "매출" in job.error_message or "정산" in job.error_message
    assert db_session.query(DailySettlement).count() == 0  # 저장된 게 없어야 함


def test_sync_isolates_settlement_failure_from_stats_success(db_session, sync_setup, monkeypatch):
    """매출/재주문율은 성공했는데 입금(정산내역)만 실패해도 매출/재주문율은
    정상 저장돼야 한다 — 항목별 독립 실패 격리."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )

    def _raise_settlement(page, start_date, end_date, **kwargs):
        raise BaeminStatsScrapeError("정산내역 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _raise_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000
    assert settlement.deposit_amount == 0  # 정산 실패라 갱신 안 됨(신규 행 기본값)
    assert "정산" in job.error_message


def test_sync_isolates_one_shop_stats_failure_from_other_sources(db_session, sync_setup, monkeypatch):
    """한 브랜드의 가게통계 조회가 실패해도 **다른 소스**(입금/재주문율)는
    정상 저장돼야 한다 — 소스별 실패 격리 원칙은 그대로 유효하다.

    2026-08-19 수정으로 이 테스트의 전제가 하나 바뀌었다. 예전에는 "실패한
    브랜드를 뺀 나머지 브랜드분 매출 합계는 저장된다"까지 기대했는데, 그게
    바로 I3에서 지적된 버그였다 — 매출은 계정 전체로 합산해 브랜드 차원 없이
    한 행에 저장하므로, 부분 합계를 저장하면 그 달이 "동기화 완료"로 굳어
    실패한 브랜드 몫이 영구 누락된다. 이제 매출만 all-or-nothing이고(전용
    회귀 테스트: test_sync_skips_sales_upsert_entirely_when_any_shop_stats_fetch_failed),
    나머지 소스의 격리는 여기서 계속 지킨다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    def _fetch_stats(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminStatsScrapeError("일시적 오류")
        return [_SALES_RESP], [_CRM_RESP]

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [_SETTLE_RESP])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.deposit_amount == 40000  # 입금은 가게통계 실패와 무관하게 정상 저장
    assert settlement.sales_amount == 0  # 부분 합계는 저장하지 않는다(I3)
    assert db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one().new_orders == 1  # 재주문율도 정상 저장
    assert "브랜드A" in job.error_message or "11111" in job.error_message


def test_sync_isolates_malformed_sales_response_from_other_three_sources(db_session, sync_setup, monkeypatch):
    """fetch_shop_stats가 200으로 성공했지만 응답 모양이 바뀌어(예: "graph" 키
    누락) map_sales_by_date가 KeyError를 던지는 현실적인 상황을 흉내낸다.
    이 매핑 단계 예외가 upsert 루프까지 감싸는 try/except 없이 새어나가면
    sync_reviews_for_job의 바깥쪽 안전망까지 번져 이미 저장된 리뷰까지
    롤백되고 나머지 세 소스(이번 달 매출/재주문율/입금)도 전혀 실행되지
    않는다 — 이번 라운드에서 고친 Critical 버그의 회귀 테스트."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    _malformed_sales_resp = {"orderAmount": 50000.0, "orderCount": 2}  # "graph" 키 누락
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_malformed_sales_resp], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date, **kwargs: [_SETTLE_RESP],
    )

    sync_reviews_for_job(job, conn, db_session)

    # (a) job은 여전히 success로 끝나야 한다 — 리뷰와 나머지 세 소스가 성공했으므로.
    assert job.status == "success"
    # (b) 이미 성공적으로 동기화된 리뷰는 롤백되지 않고 그대로 커밋돼야 한다.
    assert job.reviews_inserted == 1
    assert db_session.query(Review).filter_by(external_review_id=1001).one()
    # (c) 나머지 세 소스(이번 달 매출/재주문율/입금)는 정상적으로 저장돼야 한다.
    current_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-15",
    ).one()
    assert current_month_row.sales_amount == 12000  # 이번 달(주문내역)분

    deposit_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert deposit_row.deposit_amount == 40000  # 입금(정산내역)분, _SETTLE_RESP는 08-10 날짜

    repurchase = db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one()
    assert repurchase.new_orders == 1

    # (d) 매출(가게통계) 실패 메시지가 error_message에 남아야 한다.
    assert "매출" in job.error_message


def test_sync_marks_job_success_when_all_reviews_fail_but_stats_succeed(db_session, sync_setup, monkeypatch):
    """모든 매장의 리뷰 조회가 실패해도(succeeded_any == False), 매출 등 네
    소스 중 하나라도 성공해 실제로 DailySettlement 행이 커밋됐다면 job은
    "failed"가 아니라 "success"로 끝나야 한다 — 안 그러면 운영자가 job
    상태만 보고 실제로는 저장된 데이터가 있는데도 "아무것도 안 됐다"고
    오해하게 된다. 이번 라운드에서 고친 Important 버그의 회귀 테스트."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise_reviews(page, shop_no, **kwargs):
        raise BaeminScrapeError("리뷰 조회 실패: HTTP 500")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise_reviews)
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], []),
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"  # 이전에는 "failed"였던 회귀 지점
    assert "HTTP 500" in job.error_message  # 리뷰 실패 요약은 그대로 남아야 한다
    assert job.reviews_inserted == 0

    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000  # 매출 데이터는 실제로 커밋됨


def test_sync_isolates_non_keyerror_malformed_sales_response_from_other_three_sources(db_session, sync_setup, monkeypatch):
    """Important #1 회귀 테스트: map_sales_by_date는 KeyError뿐 아니라
    TypeError(예: graph.data[].y가 null이라 round(None)이 실패)도 던질 수
    있다. 이 예외가 KeyError 전용 except에 안 잡히고 새어나가면
    sync_reviews_for_job의 바깥쪽 안전망까지 번져 이미 저장된 리뷰와 나머지
    세 소스(이번 달 매출/재주문율/입금)까지 전부 롤백된다 — 이번 라운드에서
    (BaeminStatsScrapeError, KeyError)를 Exception으로 넓힌 수정의 회귀 테스트."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    # "graph" 키는 있지만 y가 null — round(None)이 TypeError를 던진다(KeyError가 아님).
    _malformed_sales_resp = {"graph": {"data": [{"x": "2026-08-10", "y": None}]}, "orderAmount": 50000.0, "orderCount": 2}
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_malformed_sales_resp], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date, **kwargs: [_SETTLE_RESP],
    )

    sync_reviews_for_job(job, conn, db_session)

    # (a) job은 여전히 success로 끝나야 한다.
    assert job.status == "success"
    # (b) 이미 성공적으로 동기화된 리뷰는 롤백되지 않고 그대로 커밋돼야 한다.
    assert job.reviews_inserted == 1
    assert db_session.query(Review).filter_by(external_review_id=1001).one()
    # (c) 나머지 세 소스(이번 달 매출/재주문율/입금)는 정상적으로 저장돼야 한다.
    current_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-15",
    ).one()
    assert current_month_row.sales_amount == 12000

    deposit_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert deposit_row.deposit_amount == 40000

    repurchase = db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one()
    assert repurchase.new_orders == 1

    # (d) 매출(가게통계) 실패 메시지가 error_message에 남아야 한다.
    assert "매출" in job.error_message


def test_sync_zeroes_stale_mock_deposit_on_gap_date_within_fetch_window(db_session, sync_setup, monkeypatch):
    """Important #4 회귀 테스트: 배민 정산은 배치 지급 캘린더라 주말/공휴일
    등 실제 배치가 없는 날짜(갭 날짜)는 fetch_account_settlement 응답에
    아예 등장하지 않는다. 그런 갭 날짜에 이미 있던 Mock deposit_amount를
    그대로 두면 실데이터와 조용히 섞인다 — 조회 범위 안의 기존 행은 실제
    배치를 적용하기 전에 먼저 0으로 초기화돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    today = date.today()
    gap_date = today - timedelta(days=5)  # 실제 배치 응답에 없는 갭 날짜(주말 등)를 흉내낸다
    payout_date = today - timedelta(days=3)  # 실제 배치가 있는 날짜

    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=gap_date, sales_amount=1000, deposit_amount=99999,  # 오래된 Mock 시드 값
    ))
    db_session.commit()

    settle_resp = {
        "contents": [{"giveId": 900003, "depositDueDate": payout_date.isoformat(), "giveAmount": 40000, "giveStatus": "REQUEST"}],
        "totalSize": 1,
    }
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date, **kwargs: [settle_resp])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    gap_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date=gap_date,
    ).one()
    assert gap_row.deposit_amount == 0  # 갭 날짜는 Mock이 아니라 0으로 초기화됨

    payout_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date=payout_date,
    ).one()
    assert payout_row.deposit_amount == 40000  # 실제 배치가 있는 날짜는 실제 금액으로 채워짐


def test_sync_leaves_deposit_amount_outside_fetch_window_untouched(db_session, sync_setup, monkeypatch):
    """조회 범위 밖 날짜의 기존 deposit_amount는 이번 동기화가 그 날짜를
    아예 시도조차 하지 않았으므로 손대면 안 된다.

    Task 4에서 입금 조회가 고정 90일 창에서 커서 기반 증분 범위
    (compute_settlement_sync_range)로 바뀌면서, "조회 범위 밖"이 더 이상
    "today-90일보다 예전"이 아니라 "커서(가장 최근 deposit_amount 확보
    날짜)-2일보다 예전"이 됐다 — 이 store+platform에 deposit_amount가 채워진
    행이 old_date 하나뿐이면 그 행 자체가 커서가 되어 조회 범위 안에
    들어가버린다. 그래서 별도로 최근 커서 행(recent_date)을 둬서 조회
    범위를 좁혀야 old_date가 실제로 범위 밖에 남는다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    recent_date = date.today() - timedelta(days=3)  # 커서 역할 — 조회 범위를 최근으로 좁힌다
    old_date = date.today() - timedelta(days=120)  # 좁혀진 조회 범위 밖
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=recent_date, sales_amount=0, deposit_amount=10000,
    ))
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=old_date, sales_amount=1000, deposit_amount=55555,
    ))
    db_session.commit()

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date=old_date,
    ).one()
    assert row.deposit_amount == 55555  # 조회 범위 밖 날짜는 손대지 않음


def test_sync_leaves_other_platform_deposit_amount_untouched_within_fetch_window(db_session, sync_setup, platforms, monkeypatch):
    """배민 동기화가 같은 날짜 범위 안의 요기요 행까지 건드리면 안 된다 —
    zero-out은 platform_id로 엄격히 스코프돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    gap_date = date.today() - timedelta(days=5)
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=platforms["yogiyo"].id,
        settle_date=gap_date, sales_amount=1000, deposit_amount=22222,
    ))
    db_session.commit()

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=platforms["yogiyo"].id, settle_date=gap_date,
    ).one()
    assert row.deposit_amount == 22222  # 다른 플랫폼 행은 손대지 않음


def test_upsert_brand_ad_click_metric_creates_new_row(db_session, seeded_user, platforms):
    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95
    assert row.impressions == 40


def test_upsert_brand_ad_click_metric_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    from datetime import date
    existing = BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date.fromisoformat("2026-08-01"),
        ad_spend=999, impressions=999, clicks=9, ad_orders=9, ad_revenue=9000,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    rows = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date="2026-08-01",
    ).all()
    assert len(rows) == 1
    assert rows[0].ad_spend == 95


def test_upsert_brand_ad_click_metric_leaves_other_brand_rows_untouched(db_session, seeded_user, platforms):
    from datetime import date
    other_brand = BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date=date.fromisoformat("2026-08-01"),
        ad_spend=285, impressions=40, clicks=3, ad_orders=1, ad_revenue=19900,
    )
    db_session.add(other_brand)
    db_session.commit()

    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    untouched = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date="2026-08-01",
    ).one()
    assert untouched.ad_spend == 285  # 안 건드림


_CLICK_RESP_AUGUST = {
    "summary": {"displayCount": 201, "clickCount": 6, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 2.985, "orderRate": 0.0, "spentBudget": 570, "returnOnAdSpend": 0.0},
    "metrics": {},
    "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 95, "displayCount": 40, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
    ],
}


def test_sync_upserts_brand_click_metrics_per_shop(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()  # shop_no=99999001
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_click_metrics",
        lambda page, shop_no, months: [_CLICK_RESP_AUGUST],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="99999001", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95
    assert row.clicks == 1


def test_sync_sums_nothing_across_brands_for_click_metrics(db_session, sync_setup, monkeypatch):
    """매출/재주문율과 달리 브랜드별로 완전히 분리 저장돼야 한다 — 서로 다른
    shop_no는 서로 다른 행이지 합산 대상이 아니다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    click_a = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 100, "displayCount": 10, "clickCount": 1, "orderCount": 0, "orderAmounts": 0},
    ]}
    click_b = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 200, "displayCount": 20, "clickCount": 2, "orderCount": 0, "orderAmounts": 0},
    ]}

    def _fetch_click(page, shop_no, months):
        return [click_a] if shop_no == 11111 else [click_b]

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_click)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row_a = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="11111", metric_date="2026-08-01",
    ).one()
    row_b = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="22222", metric_date="2026-08-01",
    ).one()
    assert row_a.ad_spend == 100  # 합산 안 됨, 각자 따로
    assert row_b.ad_spend == 200


def test_sync_isolates_one_brand_click_metrics_failure_from_other_brands(db_session, sync_setup, monkeypatch):
    """한 브랜드의 우리가게클릭 조회 실패(예: 캠페인이 없는 브랜드)가 다른
    브랜드의 정상 수집을 막지 않아야 한다 — 리뷰/매출과 같은 브랜드별 독립
    실패 격리 원칙."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    click_b = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 200, "displayCount": 20, "clickCount": 2, "orderCount": 0, "orderAmounts": 0},
    ]}

    def _fetch_click(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminAdsScrapeError("우리가게클릭 캠페인을 찾을 수 없습니다")
        return [click_b]

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_click)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert db_session.query(BrandAdClickMetric).filter_by(shop_no="11111").count() == 0
    row_b = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="22222", metric_date="2026-08-01",
    ).one()
    assert row_b.ad_spend == 200
    assert "우리가게클릭" in job.error_message


def test_upsert_daily_settlement_sets_breakdown_columns_on_new_row(db_session, seeded_user, platforms):
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-12",
        commission_amount=131_402, delivery_fee_amount=210_800,
        customer_discount_amount=271_760, ad_cost_amount=48_095,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-12",
    ).one()
    assert row.commission_amount == 131_402
    assert row.delivery_fee_amount == 210_800
    assert row.customer_discount_amount == 271_760
    assert row.ad_cost_amount == 48_095


def test_upsert_daily_settlement_breakdown_none_leaves_existing_value_untouched(db_session, seeded_user, platforms):
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date=date(2026, 8, 12),
        sales_amount=0, deposit_amount=0, commission_amount=999,
    )
    db_session.add(existing)
    db_session.commit()

    # sales_amount만 갱신하는 흔한 호출 — commission_amount는 안 건드려야 한다.
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-12", sales_amount=5_000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-12",
    ).one()
    assert row.sales_amount == 5_000
    assert row.commission_amount == 999  # 안 건드림


_BREAKDOWN_DETAIL = {
    "giveId": 531969790, "depositDueDate": "2026-08-12",
    "giveAmount": 904812,
    "baemin1Details": {
        "giveAmount": 936472,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -102741},
            "benefitsAmount": {"total": -266760},
        },
        "delivery": {"deliverySupplyPrice": {"total": -210800}},
        "extra": {"paymentFee": {"total": -27329}},
    },
    "baeminDetails": {
        "giveAmount": 16435,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -1081},
            "benefitsAmount": {"total": -5000},
        },
        "delivery": {"deliverySupplyPrice": {"total": 0}},
        "extra": {"paymentFee": {"total": -251}},
    },
    "etcDetails": {"total": 0},
    "cpcDetails": {"total": -48095},
}


def test_sync_upserts_settlement_breakdown_columns(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_settlement_breakdown_details",
        lambda page, start_date, end_date, **kwargs: [_BREAKDOWN_DETAIL],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-12",
    ).one()
    assert row.commission_amount == 131_402
    assert row.delivery_fee_amount == 210_800
    assert row.customer_discount_amount == 271_760
    assert row.ad_cost_amount == 48_095


def test_sync_isolates_settlement_breakdown_failure_from_deposit(db_session, sync_setup, monkeypatch):
    """상세 수집이 실패해도(예: 카드 클릭 실패) 이미 별도로 성공한
    deposit_amount(summary 기반)에는 영향이 없어야 한다 — 설계 문서 에러
    처리 표."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import BaeminStatsScrapeError

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date, **kwargs: [
            {"contents": [{"giveId": 531969790, "depositDueDate": "2026-08-12", "giveAmount": 904812}], "totalSize": 1},
        ],
    )

    def _raise(page, start_date, end_date, **kwargs):
        raise BaeminStatsScrapeError("정산 상세 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-12",
    ).one()
    assert row.deposit_amount == 904_812  # summary 기반 입금액은 영향 없음
    assert row.commission_amount is None  # 상세는 실패했으니 NULL 유지
    assert "정산 상세" in job.error_message


def test_upsert_order_creates_new_row(db_session, seeded_user, platforms):
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="[양념조절가능]숯불양념바베큐치킨", order_type="delivery", amount=15900,
    )
    db_session.commit()

    row = db_session.query(Order).filter_by(order_no="T2FE000020VQ").one()
    assert row.store_id == seeded_user["store"].id
    assert row.platform_id == platforms["baemin"].id
    assert row.menu_summary == "[양념조절가능]숯불양념바베큐치킨"
    assert row.order_type == "delivery"
    assert row.amount == 15900
    assert row.ordered_at == datetime(2026, 8, 13, 2, 19, 37)


def test_upsert_order_stores_kst_wall_clock_as_correct_absolute_instant(db_session, seeded_user, platforms):
    """배민의 `orderDateTime`은 타임존 오프셋이 없는 **한국 벽시계 시간**이고
    `orders.ordered_at`은 TIMESTAMPTZ다 — naive datetime을 그대로 넣으면
    Postgres가 UTC로 해석해 실제보다 9시간 늦은 순간으로 저장한다(15시 이후
    주문은 날짜까지 하루 밀린다, 2026-08-13 최종 리뷰에서 실데이터로 확인).
    그래서 DB로 넘어가는 값이 "한국시간 02:19:37"이라는 절대 시각을 정확히
    담고 있어야 한다.

    DB에 실제로 넘어가는 값이 타임존을 가진 절대 시각인지는
    `parse_baemin_datetime` 단위 테스트가 검증한다(test_baemin_stats.py) —
    이 스위트의 DB는 인메모리 SQLite인데 SQLite의 DATETIME 저장 포맷에는
    오프셋 자리가 아예 없어서, 커밋 후 다시 읽으면 오프셋이 사라진 naive
    값으로 돌아오기 때문이다(즉 SQLite로는 이 버그의 유무를 구분할 수 없다).
    실제 TIMESTAMPTZ 왕복은 Postgres로 라이브 검증했다(final-fix-report.md).

    여기서는 그 대신 **저장된 벽시계 숫자**를 고정한다 — 한국시간 02:19:37이
    그대로 남아야 하고, UTC로 바꿔 저장하는(17:19:37) 식의 잘못된 "수정"이
    들어오면 이 테스트가 깨진다."""
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="KST0000001", ordered_at="2026-08-13T02:19:37",
        menu_summary="심야 치킨", order_type="delivery", amount=15900,
    )
    db_session.commit()

    row = db_session.query(Order).filter_by(order_no="KST0000001").one()
    seoul_wall_clock = row.ordered_at.astimezone(ZoneInfo("Asia/Seoul")) if row.ordered_at.tzinfo else row.ordered_at
    assert (
        seoul_wall_clock.year, seoul_wall_clock.month, seoul_wall_clock.day,
        seoul_wall_clock.hour, seoul_wall_clock.minute, seoul_wall_clock.second,
    ) == (2026, 8, 13, 2, 19, 37)


def test_upsert_order_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="원래 메뉴", order_type="delivery", amount=15900,
    )
    db_session.commit()

    # 같은 order_no로 다시 upsert(예: 증분 조회의 2일 여유 구간이 겹칠 때)
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="바뀐 메뉴", order_type="takeout", amount=16900,
    )
    db_session.commit()

    rows = db_session.query(Order).filter_by(order_no="T2FE000020VQ").all()
    assert len(rows) == 1
    assert rows[0].menu_summary == "바뀐 메뉴"
    assert rows[0].order_type == "takeout"
    assert rows[0].amount == 16900


def test_upsert_order_keyed_by_order_no_alone_not_by_store_id(db_session, seeded_user, platforms):
    """order_no는 매장과 무관하게 전역 유일하다(schema.sql의 UNIQUE 제약과 동일).
    같은 order_no를 다른 store_id로 upsert하면 기존 행을 갱신해야지 새 행을 만들면 안 된다
    — upsert_daily_settlement 등과 달리 (store_id, platform_id, settle_date) 복합키가 아니라
    order_no 단독으로 식별한다."""
    from app.models import Store, User
    from app.auth import hash_password

    # 두 번째 매장 생성
    user2 = User(
        email="demo2@dris.kr", password_hash=hash_password("demo1234!"), nickname="김사장2",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user2)
    db_session.flush()

    store2 = Store(user_id=user2.id, name="피자천국", category="피자", created_at=datetime.now(timezone.utc))
    db_session.add(store2)
    db_session.commit()

    # 첫 번째 매장으로 order_no="T2FE000020VQ" 생성
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="원래 메뉴", order_type="delivery", amount=15900,
    )
    db_session.commit()

    # 두 번째 매장으로 같은 order_no 다시 upsert
    # 새 행이 만들어지지 않고 기존 행이 갱신되어야 한다 (order_no만 키)
    upsert_order(
        db_session, store2.id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T03:45:00",
        menu_summary="바뀐 메뉴 (다른 매장)", order_type="takeout", amount=22000,
    )
    db_session.commit()

    # 같은 order_no로 조회했을 때 정확히 1행만 있어야 한다 — 두 번째 upsert가 새 행을
    # 만들지 않고 기존 행을 갱신했다는 뜻이다 (order_no가 전역 유일 키임을 증명)
    rows = db_session.query(Order).filter_by(order_no="T2FE000020VQ").all()
    assert len(rows) == 1, f"Expected 1 row with order_no=T2FE000020VQ, got {len(rows)} — order_no는 전역 유일이어야 함"

    # 그 행은 두 번째 upsert의 데이터 필드로 갱신되어야 한다
    # (store_id/platform_id는 기존 값 유지 — 다른 upsert_* 함수들처럼 키 필드는 안 건드림)
    row = rows[0]
    assert row.store_id == seeded_user["store"].id, "order_no가 전역 유일이라도 첫 upsert의 store_id는 유지"
    assert row.menu_summary == "바뀐 메뉴 (다른 매장)", "데이터 필드는 최신값으로 갱신"
    assert row.order_type == "takeout", "데이터 필드는 최신값으로 갱신"
    assert row.amount == 22000, "데이터 필드는 최신값으로 갱신"


_ORDER_ITEM_A = {
    "order": {
        "orderNumber": "T2FE000020VQ", "orderDateTime": "2026-08-13T02:19:37",
        "payAmount": 15900, "itemsSummary": "숯불양념바베큐치킨", "deliveryType": "DELIVERY",
    },
}
_ORDER_ITEM_B = {
    "order": {
        "orderNumber": "B2FD00HZNU", "orderDateTime": "2026-08-10T18:02:11",
        "payAmount": 21000, "itemsSummary": "1인 숯불양념치밥 SET", "deliveryType": "TAKEOUT",
    },
}


def test_sync_upserts_individual_orders(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [_ORDER_ITEM_A, _ORDER_ITEM_B],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row_a = db_session.query(Order).filter_by(order_no="T2FE000020VQ").one()
    assert row_a.amount == 15900
    assert row_a.order_type == "delivery"
    row_b = db_session.query(Order).filter_by(order_no="B2FD00HZNU").one()
    assert row_b.order_type == "takeout"


def test_sync_uses_incremental_range_when_orders_already_exist(db_session, sync_setup, monkeypatch):
    """이미 저장된 주문이 있으면 compute_order_sync_range가 계산한 좁은
    범위로 fetch_orders를 호출해야 한다 — 3개월 전체를 다시 긁지 않는다."""
    import app.review_sync as review_sync_mod
    from datetime import date, datetime

    job, conn = sync_setup
    db_session.add(Order(
        store_id=job.store_id, platform_id=job.platform_id, order_no="OLD0000001",
        ordered_at=datetime(2026, 8, 10, 9, 0, 0),
        menu_summary="기존 주문", order_type="delivery", amount=10000,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    captured_ranges = []

    def _fake_fetch_orders(page, start_date, end_date, **kwargs):
        captured_ranges.append((start_date, end_date))
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _fake_fetch_orders)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 두 번 호출된다: "이번 달 매출 보완"(이번 달 1일~오늘)과
    # "개별 주문 저장"(증분 범위) — 둘 중 하나는 2026-08-08(8/10 - 2일)로
    # 시작해야 한다.
    assert any(r[0] == "2026-08-08" for r in captured_ranges)


def test_sync_isolates_individual_order_failure_from_current_month_sales(db_session, sync_setup, monkeypatch):
    """개별 주문 저장이 실패해도 이번 달 매출 보완(별도 fetch_orders 호출)은
    영향받지 않아야 한다 — 항목별 독립 실패 격리 원칙."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    call_count = {"n": 0}

    def _flaky_fetch_orders(page, start_date, end_date, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 첫 호출(이번 달 매출 보완)은 성공
            return [_ORDER_ITEM_A]
        # 두 번째 호출(개별 주문 저장)은 실패
        raise BaeminStatsScrapeError("주문내역 상세 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _flaky_fetch_orders)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    current_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-13",
    ).one()
    assert current_month_row.sales_amount == 15900  # 매출 보완은 정상 반영
    assert db_session.query(Order).filter_by(order_no="T2FE000020VQ").count() == 0  # 개별 주문 저장은 실패
    assert "주문내역" in job.error_message


def test_sync_raises_page_click_cap_for_deep_order_backfill(db_session, sync_setup, monkeypatch):
    """3개월 백필은 실측 1,541건(약 155페이지)이라, 기본 페이지네이션 상한으로는
    구조적으로 끝까지 도달할 수 없다 — 개별 주문 저장 호출만 상한을 크게 올려
    넘겨야 한다. 이번 달 매출 보완 호출(한 달치)은 기본값을 그대로 쓴다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import ORDER_BACKFILL_PAGE_CLICKS

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    calls = []

    def _capture(page, start_date, end_date, **kwargs):
        calls.append({"start": start_date, "end": end_date, "max_page_clicks": kwargs.get("max_page_clicks")})
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _capture)
    sync_reviews_for_job(job, conn, db_session)

    assert len(calls) == 2, "이번 달 매출 보완 + 개별 주문 저장, 두 번 호출된다"
    backfill_call = calls[-1]
    assert backfill_call["max_page_clicks"] == ORDER_BACKFILL_PAGE_CLICKS
    # 3개월(약 92일)치를 10건씩 페이지네이션하려면 최소 155페이지가 필요하다.
    assert ORDER_BACKFILL_PAGE_CLICKS >= 155
    # 이번 달 매출 보완은 기본 상한을 그대로 쓴다(명시적으로 넘기지 않는다).
    assert calls[0]["max_page_clicks"] is None


def test_sync_updates_campaign_current_cpc_from_real_bid(db_session, sync_setup, monkeypatch):
    """브랜드별로 fetch_cpc_booking이 반환한 bid로 ad_campaigns.current_cpc가
    갱신돼야 한다 — 브랜드마다 다른 값을 줘서 취급이 뒤섞이지 않는지도 함께
    확인한다. shop_no 11111/22222는 _FakeMultiShopSession의 값과 맞춘다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    campaign_a = AdCampaign(
        store_id=job.store_id, category="치킨", current_cpc=1, target_rank=3,
        status="active", shop_no="11111",
    )
    campaign_b = AdCampaign(
        store_id=job.store_id, category="찜·탕·찌개", current_cpc=1, target_rank=10,
        status="active", shop_no="22222",
    )
    db_session.add_all([campaign_a, campaign_b])
    db_session.commit()

    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    def fake_fetch_cpc_booking(page, shop_no):
        return {
            11111: {"bid": 95, "max_bid": 860, "monthly_budget": 1_000_000, "spent_budget": 150_065, "is_auto_bidding": False},
            22222: {"bid": 60, "max_bid": 500, "monthly_budget": 500_000, "spent_budget": 20_000, "is_auto_bidding": False},
        }[shop_no]

    monkeypatch.setattr(review_sync_mod, "fetch_cpc_booking", fake_fetch_cpc_booking)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    db_session.refresh(campaign_a)
    db_session.refresh(campaign_b)
    assert campaign_a.current_cpc == 95
    assert campaign_b.current_cpc == 60


def test_sync_isolates_cpc_booking_failure_from_click_metrics(db_session, sync_setup, monkeypatch):
    """CPC 입찰가 조회 실패가 같은 브랜드의 우리가게클릭 수집 성공까지
    막으면 안 된다 — 브랜드별 독립 실패 격리 원칙(리뷰/매출과 동일)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    campaign = AdCampaign(
        store_id=job.store_id, category="치킨", current_cpc=1, target_rank=3,
        status="active", shop_no="99999001",
    )
    db_session.add(campaign)
    db_session.commit()

    fake_session = _FakeSession()  # shop_no=99999001
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_click_metrics",
        lambda page, shop_no, months: [_CLICK_RESP_AUGUST],
    )

    def _raise_cpc(page, shop_no):
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_cpc_booking", _raise_cpc)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    db_session.refresh(campaign)
    assert campaign.current_cpc == 1  # 실패했으니 갱신 안 됨
    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="99999001", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95  # 클릭 성과는 CPC 실패와 무관하게 정상 수집됨


def test_sync_skips_already_synced_months_for_sales_but_still_visits_one_month_for_crm(
    db_session, sync_setup, monkeypatch,
):
    """3개월 전부(이번 달 포함 — 이번 달 몫은 "이번 달 매출 보완"이라는
    별도 경로로도 채워질 수 있으므로, 필터 입장에선 이번 달도 이미 동기화된
    것으로 보일 수 있다) 이미 sales_amount로 채워져 있어도, fetch_shop_stats에
    빈 목록을 넘기면 안 된다 — crmInfo(소급 불가능한 최근 7일 스냅샷) 캡처를
    위해 최소 1개월(가장 최근 완료된 달)은 반드시 방문해야 한다.

    2026-08-19 수정 이후 이 보장은 "결과가 비면 대체한다"는 fallback 분기가
    아니라 `filter_months_needing_sync(..., always_include={months[-2]})`에서
    나온다 — 기대 결과는 그대로지만 메커니즘이 바뀌었다(그 fallback은 완료된
    두 달만 있고 이번 달이 없는 경우 `[이번 달]` 하나만 남겨 가게통계 하드
    에러를 부르는 구멍이 있었다. I4/I5 참고)."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)  # 예: ["2026-06", "2026-07", "2026-08"]
    for m in months:  # 3개월 전부 이미 있다고 가정 → 필터 결과가 빈 리스트가 됨
        db_session.add(DailySettlement(
            store_id=job.store_id, platform_id=job.platform_id,
            settle_date=date.fromisoformat(f"{m}-15"), sales_amount=100000, deposit_amount=0,
        ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 이미 동기화된 2개월은 빠지고, crmInfo 보장용으로 마지막 완료 달(3번째 달
    # 바로 앞, 즉 months[-2]) 하나만 남아야 한다 — months[-1](이번 달)은
    # 가게통계 화면 자체가 선택 불가능해서 애초에 대상이 아니다.
    assert received_months == [[months[-2]]]


def test_sync_fetches_only_unsynced_months_when_some_are_missing(db_session, sync_setup, monkeypatch):
    """3개월 중 1개월만 이미 있으면, 나머지(아직 없는 달)만 fetch_shop_stats에
    넘겨야 한다 — crmInfo 보장 fallback은 fetch할 달이 이미 있을 때는
    끼어들지 않는다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date.fromisoformat(f"{months[0]}-15"), sales_amount=50000, deposit_amount=0,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [[months[1], months[2]]]


def test_sync_fetches_all_months_on_first_sync_when_nothing_stored_yet(db_session, sync_setup, monkeypatch):
    """최초 동기화(daily_settlements에 이 store+platform 행이 전혀 없음)는
    기존과 동일하게 3개월 전부를 fetch_shop_stats에 넘겨야 한다 — 회귀
    방지용 테스트."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [months]


def test_sync_passes_existing_review_ids_to_fetch_all_reviews(db_session, sync_setup, monkeypatch):
    """_run_sync은 이미 이 계정에 저장된 external_review_id 전체 집합을
    fetch_all_reviews에 그대로 넘겨야 한다 — 리뷰 조기종료가 실제로
    동작하려면 이 배선이 맞아야 한다."""
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

    received = {}

    def _fetch_all_reviews(page, shop_no, existing_ids=None):
        received["existing_ids"] = existing_ids
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _fetch_all_reviews)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["existing_ids"] == {1001}


def test_sync_narrows_deposit_fetch_range_when_cursor_exists(db_session, sync_setup, monkeypatch):
    """이미 deposit_amount가 채워진 가장 최근 날짜가 있으면, 90일 전체가
    아니라 그 날짜-2일부터만 fetch_account_settlement에 넘겨야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date(2026, 8, 10), sales_amount=0, deposit_amount=50000,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["range"][0] == "2026-08-08"  # 8/10 - 2일


def test_sync_uses_full_ninety_day_window_when_no_deposit_cursor_yet(db_session, sync_setup, monkeypatch):
    """이 store+platform에 deposit_amount가 채워진 행이 하나도 없으면(최초
    동기화) 기존과 동일하게 90일 전체를 조회해야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    expected_start = (date.today() - timedelta(days=90)).isoformat()
    assert received["range"][0] == expected_start


def test_sync_narrows_settlement_breakdown_range_when_cursor_exists(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date(2026, 8, 12), sales_amount=0, deposit_amount=0,
        commission_amount=1000, delivery_fee_amount=500,
        customer_discount_amount=0, ad_cost_amount=0,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received = {}

    def _fetch_settlement_breakdown_details(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _fetch_settlement_breakdown_details)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["range"][0] == "2026-08-10"  # 8/12 - 2일


def test_sync_uses_full_thirty_day_window_when_no_breakdown_cursor_yet(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received = {}

    def _fetch_settlement_breakdown_details(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _fetch_settlement_breakdown_details)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    expected_start = (date.today() - timedelta(days=30)).isoformat()
    assert received["range"][0] == expected_start


def test_sync_skips_already_synced_click_metric_months_but_always_includes_current_month(
    db_session, sync_setup, monkeypatch,
):
    """브랜드별 우가클도 매출과 같은 원리지만, 진행 중인 이번 달은 이미
    행이 있어도 항상 재조회 대상에 포함해야 한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    current_month = months[-1]
    shop_no = "99999001"  # _FakeSession.shops의 shop_no와 동일해야 함
    for m in months:  # 3개월 전부 이미 있다고 가정(이번 달 포함)
        db_session.add(BrandAdClickMetric(
            store_id=job.store_id, platform_id=job.platform_id, shop_no=shop_no,
            metric_date=date.fromisoformat(f"{m}-10"),
            ad_spend=100, impressions=10, clicks=1, ad_orders=0, ad_revenue=0,
        ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_brand_click_metrics(page, shop_no, requested_months):
        received_months.append(requested_months)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_brand_click_metrics)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 완료된 2개월은 건너뛰고, 진행 중인 이번 달만 남아야 한다.
    assert received_months == [[current_month]]


def test_sync_keeps_full_ninety_day_deposit_window_after_sales_path_wrote_todays_row(
    db_session, sync_setup, monkeypatch,
):
    """Critical 회귀 테스트(2026-08-19): 매출/이번달주문 경로가 먼저 성공해
    daily_settlements에 오늘 날짜 행을 만들어도, 입금 조회 범위는 여전히
    90일 전체여야 한다.

    원래 버그는 두 가지가 겹쳐서 났다. (1) 입금 커서 판정이
    `deposit_amount.isnot(None)`인데 이 컬럼은 `INT NOT NULL DEFAULT 0`이라
    모든 행에 항상 참인 항등식이고, (2) 매출/이번달주문 upsert가 입금 커서
    계산보다 먼저 실행되면서 `db.flush()`까지 해서 오늘 날짜 행이 이미
    존재하게 만들었다 — 그래서 최초 동기화인데도 커서가 "오늘"로 잡혀 90일
    백필이 며칠짜리로 붕괴했다. 기존 커서 테스트들은 fetch_shop_stats/
    fetch_orders를 실제로 성공시키지 않아 이 조합을 못 잡았다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    # 매출(가게통계)과 이번 달 매출(주문내역) 둘 다 실제로 값을 반환하게 해서
    # 입금 커서를 계산하기 전에 daily_settlements에 행이 실제로 생기게 만든다.
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )
    today = date.today()
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date, **kwargs: [
            {"order": {
                "orderNumber": "T-TODAY", "orderDateTime": f"{today.isoformat()}T12:00:00",
                "payAmount": 12000, "itemsSummary": "치킨", "deliveryType": "DELIVERY",
            }},
        ],
    )

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 매출 경로가 실제로 오늘 날짜 행을 만들었는지부터 확인한다 — 이게 없으면
    # 이 테스트는 버그를 재현조차 못 한 것이다.
    assert db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date=today,
    ).one().sales_amount == 12000
    assert received["range"][0] == (today - timedelta(days=90)).isoformat()


def test_sync_ignores_zero_deposit_rows_when_computing_deposit_cursor(db_session, sync_setup, monkeypatch):
    """Critical 회귀 테스트(2026-08-19): 매출만 채워지고 입금은 한 번도 안
    들어온 행(`deposit_amount=0`, NOT NULL 기본값)은 입금 커서가 되면 안
    된다 — `.isnot(None)`은 이 행도 "입금 동기화됨"으로 잡아 90일 백필을
    건너뛰게 만들었다. seed.sql의 Mock 행이 섞여 있을 때도 같은 문제다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    today = date.today()
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=today - timedelta(days=5), sales_amount=100000, deposit_amount=0,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["range"][0] == (today - timedelta(days=90)).isoformat()


def test_sync_treats_zero_sales_month_as_not_yet_synced(db_session, sync_setup, monkeypatch):
    """Critical 회귀 테스트(2026-08-19): 매출 쪽도 같은 항등식 문제를 겪었다.
    `sales_amount=0`인 행(예: seed Mock, 또는 입금만 먼저 채워진 행)이 있는
    달은 "이미 동기화됨"으로 잡히면 안 된다 — 그 달 매출을 영영 안 가져오게
    된다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date.fromisoformat(f"{months[0]}-15"), sales_amount=0, deposit_amount=50000,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [months]  # sales_amount=0인 달도 여전히 조회 대상


def test_sync_always_refetches_most_recent_completed_month_for_sales(db_session, sync_setup, monkeypatch):
    """Important 회귀 테스트(2026-08-19, I4/I5): 완료된 두 달은 이미 있고
    이번 달만 없는 흔한 상황에서, 필터 결과가 `[이번 달]` 하나만 남으면 안
    된다.

    이유 두 가지. (a) 가게통계 화면은 진행 중인 이번 달을 아예 선택할 수
    없어서(`_select_month_dropdown`이 False 반환) 이번 달만 넘기면
    `observed_sales_endpoint`가 False로 남아 하드 에러가 나고, 그 호출에서
    이미 캡처했을 crm 응답까지 예외와 함께 통째로 버려진다. (b) 이번 달
    매출은 주문내역 경로가 진행분으로 채우는데, 달이 바뀌어도 그 달엔 이미
    행이 있어 가게통계 재조회를 건너뛰면 배민의 확정 월별 수치를 영영 못
    받는다. 그래서 `months[-2]`(가장 최근 완료된 달)는 이미 동기화됐어도
    항상 포함한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    for m in months[:2]:  # 완료된 두 달만 이미 동기화됨, 이번 달은 아직 없음
        db_session.add(DailySettlement(
            store_id=job.store_id, platform_id=job.platform_id,
            settle_date=date.fromisoformat(f"{m}-15"), sales_amount=100000, deposit_amount=0,
        ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 이번 달만 남는 게 아니라, 가장 최근 완료된 달(months[-2])이 항상 함께
    # 포함돼야 한다 — 조회 달 수는 여전히 최대 2개라 성능 이득은 유지된다.
    assert received_months == [[months[-2], months[-1]]]


def test_sync_skips_sales_upsert_entirely_when_any_shop_stats_fetch_failed(
    db_session, sync_setup, monkeypatch,
):
    """Important 회귀 테스트(2026-08-19, I3): 매출은 매장별 응답을 계정 전체로
    합산해 하나의 행으로 저장하는데(daily_settlements에 브랜드 차원이 없다),
    브랜드 하나가 실패한 채로 나머지 합계만 저장하면 그 달이 "동기화 완료"로
    굳어 다음 동기화가 통째로 건너뛴다 — 실패한 브랜드 몫이 영구 누락되고
    사후 판별도 불가능하다. 그래서 완전 성공일 때만 저장한다.

    재주문율(crm)은 합산 구조가 아니고 커서로 굳지도 않으므로 이 판단과
    무관하게 계속 저장돼야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    def _fetch_stats(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminStatsScrapeError("일시적 오류")
        return [_SALES_RESP], [_CRM_RESP]

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 성공한 브랜드 몫만의 부분 합계가 저장되면 안 된다 — 아예 행이 없어야 한다.
    assert db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).count() == 0
    # 재주문율은 독립적으로 정상 저장된다.
    assert db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one().new_orders == 1
    # 왜 매출이 비었는지가 error_message로 드러나야 한다.
    assert "브랜드A" in job.error_message
    assert "매출" in job.error_message


def test_sync_fetches_all_click_metric_months_on_first_sync(db_session, sync_setup, monkeypatch):
    """이 shop_no에 brand_ad_click_metrics 행이 전혀 없으면(최초 동기화)
    기존과 동일하게 3개월 전부를 넘겨야 한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])

    received_months = []

    def _fetch_brand_click_metrics(page, shop_no, requested_months):
        received_months.append(requested_months)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_brand_click_metrics)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [months]


def test_sync_classifies_new_review_and_stores_result(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1, _RAW_2])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=1001).one()
    assert review.category == "food_quality"


def test_sync_creates_sensitive_alert_for_flagged_review(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Alert

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    alert = db_session.query(Alert).filter_by(store_id=job.store_id, alert_type="sensitive_review").one()
    assert "확인" in alert.message


def test_sync_creates_negative_review_alert_for_low_rating(db_session, sync_setup, monkeypatch):
    """seed.sql은 orders.id = reviews.order_id로 조인해 negative_review
    알림을 만들었는데, 실 배민 리뷰는 order_id가 항상 NULL이라 이 조인에
    걸리지 않아 실 연동 이후 1~2점 리뷰가 들어와도 알림이 전혀 안
    만들어지고 있었다(2026-08-25 실측 확인). 리뷰가 실제로 저장되는
    시점에 직접 만들도록 고친 회귀 테스트."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Alert

    job, conn = sync_setup
    raw_low_rating = {
        "id": 1005, "rating": 1.0, "contents": "다신 안 시켜요", "memberNickname": "화난고객",
        "orderCount": 1, "menus": [{"name": "메뉴"}], "createdAt": "2026-08-05T10:00:00+09:00",
        "displayStatus": "DISPLAY",
    }
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [raw_low_rating])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    alert = db_session.query(Alert).filter_by(store_id=job.store_id, alert_type="negative_review").one()
    assert "1점" in alert.message
    assert "다신 안 시켜요" in alert.message


def test_sync_does_not_create_negative_review_alert_for_high_rating(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Alert

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    assert db_session.query(Alert).filter_by(store_id=job.store_id, alert_type="negative_review").count() == 0


def _enable_auto_reply(db_session, store_id, style_id):
    from app.models import ReplySetting
    db_session.add(ReplySetting(
        store_id=store_id, style_id=style_id, promo_text="", include_nickname=True,
        include_menu=True, include_store_name=True, promo_on_negative=False,
        auto_reply_enabled=True, auto_reply_min_rating=1,
    ))
    db_session.commit()


def test_sync_auto_replies_to_five_star_review_when_enabled(db_session, sync_setup, reply_styles, monkeypatch):
    """자동 답글이 켜져 있으면 별점 5점 리뷰는 실제로 AI 생성 + 배민 제출까지
    자동으로 실행되고, review.status가 answered로 바뀌어야 한다."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])  # rating 5.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: "감사합니다!")
    submit_calls = []
    monkeypatch.setattr(
        review_sync_mod, "submit_reply",
        lambda page, shop_no, external_review_id, content: submit_calls.append((shop_no, external_review_id, content)),
    )

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "answered"
    assert submit_calls == [(fake_session.shop_no, _RAW_1["id"], "감사합니다!")]
    final_reply = db_session.query(ReviewReply).filter_by(review_id=review.id, reply_type="final").one()
    assert final_reply.content == "감사합니다!"


def test_sync_does_not_auto_reply_below_rating_floor(db_session, sync_setup, reply_styles, monkeypatch):
    """auto_reply_min_rating을 1로 낮게 설정해도, 지금은 별점 5점 미만은
    자동 답글 대상이 아니다(하드코딩된 안전장치, 2026-08-25)."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_2])  # rating 4.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_2["id"]).one()
    assert review.status == "unanswered"


def test_sync_does_not_auto_reply_when_category_is_not_no_issue(db_session, sync_setup, reply_styles, monkeypatch):
    """별점 5점이어도 분류 결과가 no_issue가 아니면(예: food_quality) 자동
    제출하지 않는다 — 별점만으로는 부족하다는 게 실사용으로 확인됐다
    (리뷰 id 799, 2026-08-25)."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])  # rating 5.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"


def test_sync_does_not_auto_reply_when_sensitive(db_session, sync_setup, reply_styles, monkeypatch):
    """별점 5점, category=no_issue여도 is_sensitive면 자동 제출하지 않는다."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])  # rating 5.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=True, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"


def test_sync_does_not_auto_reply_when_sentiment_conflict(db_session, sync_setup, reply_styles, monkeypatch):
    """별점 5점, category=no_issue여도 sentiment_conflict면 자동 제출하지
    않는다 — 별점-내용 불일치 리뷰는 사람이 검토해야 한다."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])  # rating 5.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=True),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"


def test_sync_does_not_auto_reply_when_disabled(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review

    job, conn = sync_setup
    # auto_reply_enabled 설정을 아예 만들지 않음(기본 상태) — reply_settings가
    # 없으면 auto_reply_style이 None으로 남아 자동 답글이 시도되지 않는다.

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"


def test_sync_does_not_auto_reply_when_owner_already_replied(db_session, sync_setup, reply_styles, monkeypatch):
    """배민에 이미 사장님 답글이 달려있는 리뷰(_RAW_ALREADY_REPLIED)는
    자동 답글을 다시 달면 안 된다(이중 답글 방지)."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_ALREADY_REPLIED])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)  # should not raise


def test_sync_auto_reply_failure_does_not_fail_whole_job(db_session, sync_setup, reply_styles, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Review
    from scrapers.baemin_reply_submit import BaeminReplySubmitError

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: "감사합니다!")

    def _raise(*a, **kw):
        raise BaeminReplySubmitError("네트워크 오류")

    monkeypatch.setattr(review_sync_mod, "submit_reply", _raise)

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"  # 실패했으니 답변 안 된 채로 남는다
    assert job.status == "success"  # 리뷰 동기화 자체는 성공
    assert "자동 답글 실패" in job.error_message


def test_sync_auto_reply_does_not_promote_to_golden_examples(db_session, sync_setup, reply_styles, monkeypatch):
    """자동 답글은 사람이 한 번도 검토하지 않은 순수 AI 산출물이라
    golden_examples로 승격하면 안 된다(순환 오염 방지)."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import GoldenExample

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: "감사합니다!")
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: None)

    sync_reviews_for_job(job, conn, db_session)

    assert db_session.query(GoldenExample).count() == 0


def test_sync_falls_back_to_default_category_when_classification_fails(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    def _raise(content, rating):
        raise review_sync_mod.ClassificationError("API 다운")

    monkeypatch.setattr(review_sync_mod, "classify_review", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"  # 분류 실패가 동기화 자체를 막지 않는다
    review = db_session.query(Review).filter_by(external_review_id=1001).one()
    assert review.category == "no_issue"
    assert review.is_sensitive is False


_MENU_DATA = {
    "store_intro": "100% 순살 닭다리살만 씁니다.",
    "food_origin": "닭고기(국내산)",
    "menu_intro": "야들야들한 닭다리살",
    "menu_items": [{"name": "치킨마요", "desc": "", "composition": "치킨+마요+밥", "price": 8900}],
}


def test_sync_fetches_and_stores_brand_menu_info_when_missing(db_session, sync_setup, monkeypatch):
    """브랜드의 brand_menu_info가 아직 없으면(최초 동기화) 메뉴 정보를
    가져와 저장해야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_menu_info", lambda page, shop_no: _MENU_DATA)

    sync_reviews_for_job(job, conn, db_session)

    info = db_session.query(BrandMenuInfo).filter_by(connection_id=conn.id, shop_no=str(_FakeSession.shop_no)).one()
    assert info.store_intro == _MENU_DATA["store_intro"]
    assert info.food_origin == _MENU_DATA["food_origin"]
    assert info.menu_intro == _MENU_DATA["menu_intro"]
    assert info.menu_items == _MENU_DATA["menu_items"]


def test_sync_skips_brand_menu_info_fetch_when_recently_synced(db_session, sync_setup, monkeypatch):
    """메뉴는 거의 안 바뀌므로 최근에(_MENU_INFO_MAX_AGE_DAYS 이내) 이미
    동기화됐으면 다시 가져오면 안 된다 — 카드 클릭 비용 낭비."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(BrandMenuInfo(
        connection_id=conn.id, shop_no=str(_FakeSession.shop_no),
        store_intro="기존 소개", food_origin="기존 원산지", menu_intro="기존 메뉴 소개",
        menu_items=[], updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_menu_info",
        lambda page, shop_no: pytest.fail("should not be called"),
    )

    sync_reviews_for_job(job, conn, db_session)

    info = db_session.query(BrandMenuInfo).filter_by(connection_id=conn.id, shop_no=str(_FakeSession.shop_no)).one()
    assert info.store_intro == "기존 소개"  # 그대로 유지 — 재조회 안 함


def test_sync_refetches_brand_menu_info_when_stale(db_session, sync_setup, monkeypatch):
    """_MENU_INFO_MAX_AGE_DAYS보다 오래된 메뉴 정보는 다시 가져와야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(BrandMenuInfo(
        connection_id=conn.id, shop_no=str(_FakeSession.shop_no),
        store_intro="오래된 소개", food_origin="오래된 원산지", menu_intro="오래된 메뉴 소개",
        menu_items=[], updated_at=datetime.now(timezone.utc) - timedelta(days=31),
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_menu_info", lambda page, shop_no: _MENU_DATA)

    sync_reviews_for_job(job, conn, db_session)

    info = db_session.query(BrandMenuInfo).filter_by(connection_id=conn.id, shop_no=str(_FakeSession.shop_no)).one()
    assert info.store_intro == _MENU_DATA["store_intro"]  # 갱신됨


def test_sync_menu_info_failure_does_not_fail_whole_job(db_session, sync_setup, monkeypatch):
    """메뉴 정보 동기화 실패는 매출/재주문율 실패와 같은 부분 실패로
    다뤄야 한다 — 리뷰 동기화 자체를 막으면 안 된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    def _raise(page, shop_no):
        raise BaeminMenuScrapeError("메뉴관리 페이지 이동에 실패했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_brand_menu_info", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert "메뉴 정보 동기화 실패" in job.error_message
    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review is not None  # 리뷰 동기화는 정상 진행됨
    assert db_session.query(BrandMenuInfo).count() == 0
