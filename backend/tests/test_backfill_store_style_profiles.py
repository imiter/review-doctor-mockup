from datetime import datetime, timezone

from app.llm import style_profile
from app.models import GoldenExample, StoreStyleProfile
from scripts.backfill_store_style_profiles import backfill_store_style_profiles


def test_backfill_recreates_profile_for_each_store_category_pair(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add_all([
        GoldenExample(
            store_id=sid, category="hygiene", review_text="이물질", reply_text="죄송합니다",
            is_manual=True, is_synthetic=False, source="backfill",
            created_at=datetime.now(timezone.utc),
        ),
        GoldenExample(
            store_id=sid, category="delivery", review_text="배달 지연", reply_text="불편드려 죄송합니다",
            is_manual=True, is_synthetic=False, source="backfill",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "- 요약")

    result = backfill_store_style_profiles(db_session)

    assert result == {"refreshed": 2, "failed": 0, "total": 2}
    assert db_session.get(StoreStyleProfile, (sid, "hygiene")) is not None
    assert db_session.get(StoreStyleProfile, (sid, "delivery")) is not None


def test_backfill_is_idempotent(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="이물질", reply_text="죄송합니다",
        is_manual=True, is_synthetic=False, source="backfill",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "- 요약")

    backfill_store_style_profiles(db_session)
    result = backfill_store_style_profiles(db_session)

    assert result == {"refreshed": 1, "failed": 0, "total": 1}
    assert db_session.query(StoreStyleProfile).filter_by(store_id=sid, category="hygiene").count() == 1
