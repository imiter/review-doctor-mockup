from datetime import datetime, timezone

from app.llm.classify import ReviewClassification
from app.models import Alert, Review
from scripts.backfill_review_classification import backfill_review_classification


def _make_review(db_session, store_id, platform_id, *, rating, content, status="unanswered", is_sensitive=False):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="치킨", rating=rating,
        content=content, customer_nickname="손님", status=status, is_sensitive=is_sensitive,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    return review


def test_backfill_reclassifies_stale_default_review(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_review_classification as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    review = _make_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요",
    )
    db_session.commit()

    result = backfill_review_classification(db_session, seeded_user["store"].id)

    assert result["reclassified"] == 1
    db_session.refresh(review)
    assert review.category == "hygiene"
    assert review.is_sensitive is True


def test_backfill_alerts_newly_sensitive_unanswered_review(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_review_classification as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    _make_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", status="unanswered", is_sensitive=False,
    )
    db_session.commit()

    result = backfill_review_classification(db_session, seeded_user["store"].id)

    assert result["newly_sensitive_alerted"] == 1
    alert = db_session.query(Alert).one()
    assert alert.alert_type == "sensitive_review"


def test_backfill_does_not_alert_for_already_answered_review(db_session, seeded_user, platforms, monkeypatch):
    """이미 종결된(answered) 리뷰가 재분류로 새로 sensitive가 되어도
    지금 와서 알림을 띄우면 사장님이 혼란스러우므로 만들지 않는다."""
    from scripts import backfill_review_classification as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    _make_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", status="answered", is_sensitive=False,
    )
    db_session.commit()

    result = backfill_review_classification(db_session, seeded_user["store"].id)

    assert result["newly_sensitive_alerted"] == 0
    assert db_session.query(Alert).count() == 0


def test_backfill_does_not_realert_already_sensitive_review(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_review_classification as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    _make_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", status="unanswered", is_sensitive=True,
    )
    db_session.commit()

    result = backfill_review_classification(db_session, seeded_user["store"].id)

    assert result["newly_sensitive_alerted"] == 0
    assert db_session.query(Alert).count() == 0


def test_backfill_counts_classification_failures_without_crashing(db_session, seeded_user, platforms, monkeypatch):
    from app.llm.classify import ClassificationError
    from scripts import backfill_review_classification as backfill_mod

    def _raise(content, rating):
        raise ClassificationError("API 오류")

    monkeypatch.setattr(backfill_mod, "classify_review", _raise)
    review = _make_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=5, content="맛있어요",
    )
    db_session.commit()

    result = backfill_review_classification(db_session, seeded_user["store"].id)

    assert result["reclassified"] == 0
    assert result["failed"] == 1
    db_session.refresh(review)
    assert review.category == "no_issue"  # 값이 바뀌지 않고 그대로 남는다


def test_backfill_only_touches_target_store(db_session, seeded_user, platforms, monkeypatch):
    from app.auth import hash_password
    from app.models import Store, User
    from scripts import backfill_review_classification as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )

    other = User(email="other@test.com", password_hash=hash_password("x"), nickname="다른사장", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="다른가게", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_review = _make_review(db_session, other_store.id, platforms["baemin"].id, rating=1, content="별로예요")
    db_session.commit()

    backfill_review_classification(db_session, seeded_user["store"].id)

    db_session.refresh(other_review)
    assert other_review.category == "no_issue"  # 다른 매장 리뷰는 건드리지 않는다
