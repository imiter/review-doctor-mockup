from datetime import datetime

from app.models import Owner, Platform, ReplyStyle, ReplyTemplate, Review, ReviewReply, Store, StorePlatform


def make_sp(db_session):
    owner = Owner(name="김사장", phone="010-0000-0000")
    store = Store(owner=owner, name="우리치킨 1호점", address="서울시 어딘가 1")
    platform = Platform(code="baemin", name="배달의민족", default_commission_rate=0.068)
    sp = StorePlatform(store=store, platform=platform, platform_store_name="우리치킨-강남")
    db_session.add(sp)
    db_session.flush()
    return sp


def test_review_defaults_and_reply(db_session):
    sp = make_sp(db_session)
    review = Review(
        store_platform_id=sp.id,
        rating=5,
        content="맛있어요",
        reviewer_name="먹보",
        has_photo=False,
        created_at=datetime(2026, 7, 20, 18, 0),
    )
    db_session.add(review)
    db_session.flush()
    assert review.status == "unanswered"
    assert review.reply is None

    style = ReplyStyle(name="친근함", description="따뜻하고 다정한 말투")
    db_session.add(style)
    db_session.flush()
    reply = ReviewReply(
        review_id=review.id, style_id=style.id, content="감사합니다!",
        created_at=datetime(2026, 7, 21, 9, 0),
    )
    db_session.add(reply)
    db_session.flush()
    db_session.refresh(review)
    assert review.reply.content == "감사합니다!"


def test_template_band(db_session):
    style = ReplyStyle(name="정중함", description="격식 있는 말투")
    db_session.add(style)
    db_session.flush()
    tpl = ReplyTemplate(style_id=style.id, rating_band="high", template_text="{reviewer_name}님 감사합니다.")
    db_session.add(tpl)
    db_session.flush()
    assert tpl.rating_band == "high"
