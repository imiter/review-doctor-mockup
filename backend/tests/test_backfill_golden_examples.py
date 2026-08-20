from datetime import datetime, timezone

from app.llm.classify import ReviewClassification
from app.models import GoldenExample, Review, ReviewReply
from scripts.backfill_golden_examples import backfill_negative_review_replies


def _make_answered_review(db_session, store_id, platform_id, *, rating, content, reply_content):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="치킨", rating=rating,
        content=content, customer_nickname="손님", status="answered",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewReply(
        review_id=review.id, reply_type="final", style_id=None,
        content=reply_content, created_at=datetime.now(timezone.utc),
    ))
    return review


def test_backfill_creates_golden_example_for_each_low_rating_reply(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", reply_content="확인해서 조치하겠습니다",
    )
    db_session.commit()

    count = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert count == 1
    example = db_session.query(GoldenExample).one()
    assert example.category == "hygiene"
    assert example.is_manual is True
    assert example.is_synthetic is False
    assert example.source == "backfill"
    assert example.reply_text == "확인해서 조치하겠습니다"


def test_backfill_skips_reviews_above_threshold_rating(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )
    _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=4, content="맛있어요", reply_content="감사합니다",
    )
    db_session.commit()

    count = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert count == 0
    assert db_session.query(GoldenExample).count() == 0


def test_backfill_is_idempotent(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    review = _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", reply_content="확인해서 조치하겠습니다",
    )
    db_session.commit()

    first = backfill_negative_review_replies(db_session, seeded_user["store"].id)
    second = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert first == 1
    assert second == 0  # 같은 review에 대해 두 번 넣지 않는다
    assert db_session.query(GoldenExample).filter_by(source_review_id=review.id).count() == 1
