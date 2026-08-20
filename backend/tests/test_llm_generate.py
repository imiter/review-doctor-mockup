from datetime import datetime, timezone

from app.llm import generate
from app.models import GoldenExample, Review, StoreStyleProfile


def test_generate_ai_reply_includes_style_profile_and_examples(db_session, seeded_user, platforms, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="- 구체적 원인을 설명한다", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="옛날 리뷰", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=3, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return "죄송합니다, 확인하겠습니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"])

    assert result == "죄송합니다, 확인하겠습니다."
    assert "구체적 원인을 설명한다" in captured["system"]
    assert "옛날 리뷰" in captured["system"]
    assert "내용을 그대로 복사하지" in captured["system"]  # 안전장치 지시가 포함됐는지
    assert "재방문" in captured["user"] or "3회" in captured["user"]  # 재방문 고객 정보 반영
    assert "이물질이 나왔어요" in captured["user"]


def test_generate_ai_reply_without_style_profile_uses_fallback_instruction(db_session, seeded_user, platforms, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=2, content="배달이 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    def _fake_call_sonnet(system, user, **kw):
        return "죄송합니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"])

    assert result == "죄송합니다."


def test_generate_ai_reply_injects_sensitive_instruction(db_session, seeded_user, platforms, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"])

    assert "민감" in captured["user"] or "신중" in captured["user"]
