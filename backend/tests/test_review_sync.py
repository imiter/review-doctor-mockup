from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import CredentialCryptoError, encrypt_credential
from app.models import BaeminShopBrand, BrandAdClickMetric, DailySettlement, RepurchaseMetric, Review, ReviewReply, ReviewSyncJob, StorePlatformConnection
from app.review_sync import sync_reviews_for_job, upsert_brand_ad_click_metric, upsert_daily_settlement, upsert_repurchase_metric
from scrapers.baemin_ads import BaeminAdsScrapeError
from scrapers.baemin_auth import BaeminLoginError
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
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", lambda page, shop_no, months: [])

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
        lambda page, shop_no: [_RAW_ALREADY_REPLIED],
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
    "contents": [{"depositDueDate": "2026-08-10", "giveAmount": 40000, "giveStatus": "REQUEST"}],
    "totalSize": 1,
}


def test_sync_upserts_sales_deposit_repurchase_when_all_succeed(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [_SETTLE_RESP],
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], []),  # 2026-08-10에 50000원 (완료된 달분)
    )
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_current_month_orders",
        lambda page: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], []),
    )
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])

    def _raise_current_month(page):
        raise BaeminStatsScrapeError("주문내역 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", _raise_current_month)

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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(
        review_sync_mod, "fetch_current_month_orders",
        lambda page: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [
            {"contents": [{"depositDueDate": "2026-08-15", "giveAmount": 9000, "giveStatus": "REQUEST"}], "totalSize": 1},
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    sales_a = {"graph": {"data": [{"x": "2026-08-10", "y": 30000.0}]}, "orderAmount": 30000.0, "orderCount": 1}
    sales_b = {"graph": {"data": [{"x": "2026-08-10", "y": 20000.0}]}, "orderAmount": 20000.0, "orderCount": 1}

    def _fetch_stats(page, shop_no, months):
        return ([sales_a], [_CRM_RESP]) if shop_no == 11111 else ([sales_b], [_CRM_RESP])

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [_SETTLE_RESP])

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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [_RAW_1])

    def _raise_stats(page, shop_no, months):
        raise BaeminStatsScrapeError("매출 통계 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _raise_stats)

    def _raise_settlement(page, start_date, end_date):
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )

    def _raise_settlement(page, start_date, end_date):
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


def test_sync_isolates_one_shop_stats_failure_from_other_shops(db_session, sync_setup, monkeypatch):
    """4개 브랜드 중 한 브랜드의 매출/재주문율 조회만 실패해도 나머지
    브랜드분은 정상 합산돼야 한다(리뷰의 매장별 실패 격리와 동일 원칙)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    def _fetch_stats(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminStatsScrapeError("일시적 오류")
        return [_SALES_RESP], [_CRM_RESP]

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [_SETTLE_RESP])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000  # 22222분만 반영, 11111은 실패라 제외
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [_RAW_1])

    _malformed_sales_resp = {"orderAmount": 50000.0, "orderCount": 2}  # "graph" 키 누락
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_malformed_sales_resp], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_current_month_orders",
        lambda page: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [_SETTLE_RESP],
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

    def _raise_reviews(page, shop_no):
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [_RAW_1])

    # "graph" 키는 있지만 y가 null — round(None)이 TypeError를 던진다(KeyError가 아님).
    _malformed_sales_resp = {"graph": {"data": [{"x": "2026-08-10", "y": None}]}, "orderAmount": 50000.0, "orderCount": 2}
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_malformed_sales_resp], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_current_month_orders",
        lambda page: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [_SETTLE_RESP],
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    today = date.today()
    gap_date = today - timedelta(days=5)  # 실제 배치 응답에 없는 갭 날짜(주말 등)를 흉내낸다
    payout_date = today - timedelta(days=3)  # 실제 배치가 있는 날짜

    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=gap_date, sales_amount=1000, deposit_amount=99999,  # 오래된 Mock 시드 값
    ))
    db_session.commit()

    settle_resp = {
        "contents": [{"depositDueDate": payout_date.isoformat(), "giveAmount": 40000, "giveStatus": "REQUEST"}],
        "totalSize": 1,
    }
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [settle_resp])

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
    """조회 범위(today-90일 ~ today) 밖 날짜의 기존 deposit_amount는 이번
    동기화가 그 날짜를 아예 시도조차 하지 않았으므로 손대면 안 된다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    old_date = date.today() - timedelta(days=120)  # 90일 조회 범위 밖
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

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
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

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
