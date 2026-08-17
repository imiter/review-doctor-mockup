from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Review, ReviewReply, Store, Subscription
from app.plan import add_one_month, effective_plan, kst_today, replies_used_today


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


def test_replies_used_today_counts_only_this_user_and_today(db_session, seeded_user, platforms, reply_styles):
    other_store = Store(user_id=999999, name="다른가게", category="한식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    other_review = Review(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([review, other_review])
    db_session.flush()

    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday_kst = now_kst.replace(hour=12) - timedelta(days=1)

    db_session.add_all([
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글1", created_at=now_kst),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글2", created_at=now_kst),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="어제 답글", created_at=yesterday_kst),
        ReviewReply(review_id=other_review.id, reply_type="ai_draft", content="남의 답글", created_at=now_kst),
    ])
    db_session.commit()

    assert replies_used_today(seeded_user["user"], db_session) == 2
