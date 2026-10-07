from datetime import datetime, timezone

from app.models import StoreStyleProfile


def test_store_style_profile_composite_key_allows_multiple_categories_per_store(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add_all([
        StoreStyleProfile(
            store_id=sid, category="delivery", rules="배달 원칙", generated_from_count=2,
            needs_confirmation=True, updated_at=datetime.now(timezone.utc),
        ),
        StoreStyleProfile(
            store_id=sid, category="food_quality", rules="맛 원칙", generated_from_count=3,
            needs_confirmation=False, updated_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    rows = db_session.query(StoreStyleProfile).filter_by(store_id=sid).order_by(StoreStyleProfile.category).all()
    assert [r.category for r in rows] == ["delivery", "food_quality"]
    assert rows[0].rules == "배달 원칙"
    assert rows[0].needs_confirmation is True
    assert rows[1].needs_confirmation is False


def test_store_style_profile_lookup_by_composite_key(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="위생 원칙", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fetched = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert fetched is not None
    assert fetched.rules == "위생 원칙"
    assert db_session.get(StoreStyleProfile, (sid, "service")) is None
