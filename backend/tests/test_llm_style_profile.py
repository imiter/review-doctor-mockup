from datetime import datetime, timezone

from app.llm import style_profile
from app.models import GoldenExample, StoreStyleProfile


def _make_example(db_session, store_id, *, is_manual, is_synthetic):
    ex = GoldenExample(
        store_id=store_id, category="hygiene", review_text="이물질이 나왔어요",
        reply_text="겉불을 쎄게 조리해서 그런 것 같습니다, 죄송합니다",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    return ex


def test_refresh_creates_profile_from_manual_examples_only(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    _make_example(db_session, sid, is_manual=False, is_synthetic=True)  # 이건 반영되면 안 됨
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "- 구체적 원인을 설명한다\n- 재방문 고객을 언급한다"

    monkeypatch.setattr(style_profile.client, "call_sonnet", _fake_call_sonnet)

    style_profile.refresh_store_style_profile(db_session, sid)

    profile = db_session.query(StoreStyleProfile).filter_by(store_id=sid).one()
    assert "구체적 원인" in profile.rules
    assert profile.generated_from_count == 1  # is_synthetic 예시는 제외
    assert "이물질이 나왔어요" in captured["user"]


def test_refresh_updates_existing_profile(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="옛날 규칙", generated_from_count=1, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()

    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "새 규칙")

    style_profile.refresh_store_style_profile(db_session, sid)

    profile = db_session.query(StoreStyleProfile).filter_by(store_id=sid).one()
    assert profile.rules == "새 규칙"


def test_refresh_noop_when_no_manual_examples(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    calls = []
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: calls.append(1) or "무시됨")

    style_profile.refresh_store_style_profile(db_session, sid)

    assert calls == []  # 예시가 없으면 API 호출 자체를 안 함
    assert db_session.query(StoreStyleProfile).filter_by(store_id=sid).first() is None
