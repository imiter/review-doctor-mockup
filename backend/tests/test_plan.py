from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Review, ReviewReply, Store, Subscription
from app.plan import add_one_month, effective_plan, kst_today, replies_used_today


class _FrozenDatetime(datetime):
    """KST 03:00으로 시각을 고정한다 — 이 시간대(KST 00:00~08:59)는
    UTC 날짜와 KST 날짜가 어긋나는 구간이라, _kst_today_range()가
    KST 오프셋을 UTC로 정규화하지 않으면 반드시 틀린 결과가 나온다."""

    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 1, 15, 3, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        return fixed.astimezone(tz) if tz else fixed


def test_add_one_month_normal():
    assert add_one_month(date(2026, 3, 15)) == date(2026, 4, 15)


def test_add_one_month_clamps_month_end():
    # 1월 31일 + 1개월은 2월 31일이 없으므로 2월 28일(2026은 평년)로 클램핑
    assert add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)


def test_add_one_month_year_rollover():
    assert add_one_month(date(2026, 12, 10)) == date(2027, 1, 10)


def test_effective_plan_none_subscription_is_basic():
    assert effective_plan(None) == "basic"


def test_effective_plan_pro_not_expired():
    sub = Subscription(plan="pro", expires_at=kst_today())
    assert effective_plan(sub) == "pro"


def test_effective_plan_pro_expired_falls_back_to_basic():
    sub = Subscription(plan="pro", expires_at=date(2020, 1, 1))
    assert effective_plan(sub) == "basic"


def test_effective_plan_pro_without_expiry_is_basic():
    # pro인데 expires_at이 없는 상태는 정상 흐름상 나오지 않지만, 방어적으로 basic 취급
    sub = Subscription(plan="pro", expires_at=None)
    assert effective_plan(sub) == "basic"


def test_replies_used_today_counts_only_this_user_and_today(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    import app.plan as plan_module
    monkeypatch.setattr(plan_module, "datetime", _FrozenDatetime)

    # 이 시점부터는 kst_today()/_kst_today_range()가 전부 "KST 2026-01-15 03:00"을 기준으로 계산된다.
    other_store = Store(user_id=999999, name="다른가게", category="한식", created_at=_FrozenDatetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=_FrozenDatetime.now(timezone.utc),
    )
    other_review = Review(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=_FrozenDatetime.now(timezone.utc),
    )
    db_session.add_all([review, other_review])
    db_session.flush()

    # 프로덕션 코드(generate_reply)처럼 UTC-aware로 삽입, 고정 시각 기준
    now_utc = _FrozenDatetime.now(timezone.utc)
    yesterday_utc = now_utc.replace(hour=12) - timedelta(days=1)

    db_session.add_all([
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글1", created_at=now_utc),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글2", created_at=now_utc),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="어제 답글", created_at=yesterday_utc),
        ReviewReply(review_id=other_review.id, reply_type="ai_draft", content="남의 답글", created_at=now_utc),
    ])
    db_session.commit()

    assert replies_used_today(seeded_user["user"], db_session) == 2
