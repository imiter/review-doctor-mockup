from datetime import datetime, timedelta, timezone

from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, Review


def _make_example(db_session, store_id, *, category, is_manual, is_synthetic, created_at):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text="리뷰", reply_text="답글",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        created_at=created_at,
    )
    db_session.add(ex)
    return ex


def test_fetch_golden_examples_prefers_real_over_synthetic(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    real = _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", limit=3)

    assert len(result) == 1
    assert result[0].id == real.id


def test_fetch_golden_examples_backfills_with_synthetic_when_real_insufficient(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", limit=2)

    assert len(result) == 2
    assert result[0].is_manual is True
    assert result[1].is_synthetic is True


def test_fetch_golden_examples_filters_by_category(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="delivery", is_manual=True, is_synthetic=False, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", limit=3)

    assert len(result) == 1
    assert result[0].category == "delivery"


def test_count_recent_same_category_within_window(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="배달 늦어요",
        customer_nickname="손님", category="delivery", created_at=now - timedelta(days=5),
    ))
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="또 배달 늦어요",
        customer_nickname="손님2", category="delivery", created_at=now - timedelta(days=40),  # 창 밖
    ))
    db_session.commit()

    count = count_recent_same_category(db_session, sid, "delivery", days=30)

    assert count == 1
