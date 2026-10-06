from datetime import datetime, timezone

from app.llm.agent_graph import generate_draft_node, retrieve_memory_node
from app.models import Review


def test_retrieve_memory_node_populates_state(db_session, seeded_user, platforms, reply_styles):
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=platforms["baemin"].id, menu_summary="치킨", rating=5,
        content="맛있어요", customer_nickname="손님", category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    state = {"db": db_session, "review": review, "store": store, "style": reply_styles}
    result = retrieve_memory_node(state)

    assert "rules" in result and "complaint_tone_override" in result["rules"]
    assert "style_rules" in result
    assert "examples" in result
    assert "tone_instruction" in result
    assert result["tone_overridden"] is False  # no_issue + not sensitive + not conflict
    assert "display_name" in result
    assert result["display_name"] == store.name
    assert result["category_label"] == "no_issue"
    assert result["repeat_count"] == 1  # 방금 커밋한 리뷰 자신이 집계에 포함됨
    assert result["menu_context"] is None  # platform_shop_no 없음
    assert result["retry_count"] == 0


def test_retrieve_memory_node_overrides_tone_for_complaint_review(db_session, seeded_user, platforms, reply_styles):
    """기존 generate_ai_reply와 동일하게, no_issue가 아닌 리뷰는 페르소나
    톤 대신 complaint_tone_override로 강제 전환돼야 한다."""
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=platforms["baemin"].id, menu_summary="치킨", rating=2,
        content="배달이 늦었어요", customer_nickname="손님", category="delivery",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    state = {"db": db_session, "review": review, "store": store, "style": reply_styles}
    result = retrieve_memory_node(state)

    assert result["tone_overridden"] is True
    assert result["tone_instruction"].startswith(result["rules"]["complaint_tone_override"])
    assert result["rules"]["delivery_boundary"] in result["tone_instruction"]


def test_generate_draft_node_calls_sonnet_and_strips_emoji_for_overridden_tone(monkeypatch):
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "답글 내용 😊",
    )
    state = {
        "display_name": "치킨대장", "style_rules": "규칙", "examples": [],
        "tone_instruction": "차분하게", "rules": {"menu_grounding": "m", "few_shot_anti_overfit": "f", "no_issue_framing": "n"},
        "menu_context": None, "tone_overridden": True,
        "review": type("R", (), {"rating": 1, "category": "food_quality", "content": "맛없어요", "customer_order_count": 1, "is_sensitive": False})(),
        "category_label": "음식 품질", "repeat_count": 0,
    }

    result = generate_draft_node(state)

    assert result["draft"] == "답글 내용"  # 이모지 제거됨


def test_generate_draft_node_keeps_emoji_when_tone_not_overridden(monkeypatch):
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "감사합니다 😊",
    )
    state = {
        "display_name": "치킨대장", "style_rules": "규칙", "examples": [],
        "tone_instruction": "밝게", "rules": {"menu_grounding": "m", "few_shot_anti_overfit": "f", "no_issue_framing": "n"},
        "menu_context": None, "tone_overridden": False,
        "review": type("R", (), {"rating": 5, "category": "no_issue", "content": "맛있어요", "customer_order_count": 1, "is_sensitive": False})(),
        "category_label": "no_issue", "repeat_count": 0,
    }

    result = generate_draft_node(state)

    assert result["draft"] == "감사합니다 😊"  # no_issue는 이모지를 그대로 둔다


def test_generate_draft_node_appends_extra_instruction_to_user_message(monkeypatch):
    """fix_draft(Task 3)가 좁은 재지시를 추가할 때 쓸 extra_instruction
    경로 — user_message 끝에 그대로 덧붙여야 한다."""
    captured = {}

    def _fake_call(system, user, max_tokens):
        captured["user"] = user
        return "응답"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)
    state = {
        "display_name": "치킨대장", "style_rules": "규칙", "examples": [],
        "tone_instruction": "차분하게", "rules": {"menu_grounding": "m", "few_shot_anti_overfit": "f", "no_issue_framing": "n"},
        "menu_context": None, "tone_overridden": False,
        "review": type("R", (), {"rating": 5, "category": "no_issue", "content": "맛있어요", "customer_order_count": 1, "is_sensitive": False})(),
        "category_label": "no_issue", "repeat_count": 0,
    }

    generate_draft_node(state, extra_instruction="예시를 그대로 복사하지 마라")

    assert "예시를 그대로 복사하지 마라" in captured["user"]
