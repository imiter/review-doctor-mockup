import difflib
import uuid
from datetime import datetime, timezone

import pytest

from app.llm.agent_graph import (
    fix_draft_node,
    generate_draft_node,
    retrieve_memory_node,
    route_after_verify,
    run_agent,
    verify_draft_node,
)
from app.models import GoldenExample, Review


def _golden(
    reply_text: str, *, category: str = "food_quality", store_id: int = 1, id: int = 1,
) -> GoldenExample:
    """영속화하지 않는 GoldenExample — 노드 단위 테스트는 state에 객체를
    직접 꽂아 넣기만 하므로 DB가 필요 없다."""
    return GoldenExample(
        id=id, store_id=store_id, category=category, review_text="r",
        reply_text=reply_text, is_manual=True, is_synthetic=False, source="organic",
    )


def _fake_review(*, rating: int = 5, category: str = "no_issue"):
    return type(
        "R", (),
        {"rating": rating, "category": category, "content": "c",
         "customer_order_count": 1, "is_sensitive": False},
    )()


def _draft_state(**overrides) -> dict:
    """fix_draft가 복붙 위반으로 재생성할 때 generate_draft_node가 읽는
    키 전부를 담은 기본 state."""
    state = {
        "draft": "초안", "violations": [], "copy_paste_match": None, "retry_count": 0,
        "display_name": "치킨대장", "style_rules": "s", "examples": [],
        "tone_instruction": "t",
        "rules": {"menu_grounding": "m", "few_shot_anti_overfit": "f", "no_issue_framing": "n"},
        "menu_context": None, "tone_overridden": False,
        "review": _fake_review(), "category_label": "무난", "repeat_count": 0,
    }
    state.update(overrides)
    return state


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


# ---------------------------------------------------------------------------
# verify_draft_node — 결정론적 체크 둘(이모지 / 복붙)만, LLM 재판단 없음
# ---------------------------------------------------------------------------


def test_verify_draft_flags_emoji_violation_when_tone_overridden():
    state = {"draft": "죄송합니다 😊", "tone_overridden": True, "examples": [], "display_name": ""}

    result = verify_draft_node(state)

    assert "emoji" in result["violations"]


def test_verify_draft_ignores_emoji_when_tone_not_overridden():
    """no_issue(칭찬/무난) 리뷰는 페르소나 톤의 이모지를 그대로 쓴다 —
    이모지가 있다는 사실만으로 위반이 되면 안 된다."""
    state = {"draft": "감사합니다 😊", "tone_overridden": False, "examples": [], "display_name": ""}

    result = verify_draft_node(state)

    assert result["violations"] == []


def test_verify_draft_flags_copy_paste_violation():
    example = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")
    state = {
        "draft": "불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.",
        "tone_overridden": False, "examples": [example], "display_name": "",
    }

    result = verify_draft_node(state)

    assert "copy_paste" in result["violations"]
    assert result["copy_paste_match"] is example


def test_verify_draft_flags_near_verbatim_copy_not_only_exact():
    """글자 하나 바꿔 임계값을 피해가는 경우까지 잡아야 한다 —
    SequenceMatcher 비율 0.95(실측)."""
    example = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")
    state = {
        "draft": "불편을 드려 정말 죄송합니다. 다음에는 더 신경쓰겠습니다.",
        "tone_overridden": False, "examples": [example], "display_name": "",
    }

    result = verify_draft_node(state)

    assert "copy_paste" in result["violations"]


def test_verify_draft_allows_paraphrase_of_example():
    """같은 상황에 대한 다른 표현(실측 비율 0.34)은 위반이 아니다 —
    임계값이 너무 낮으면 정상 생성물까지 전부 재시도에 걸린다."""
    example = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")
    state = {
        "draft": "맛이 기대에 못 미쳐 속상하셨겠어요. 조리 과정을 다시 점검하겠습니다.",
        "tone_overridden": False, "examples": [example], "display_name": "",
    }

    result = verify_draft_node(state)

    assert result["violations"] == []
    assert result["copy_paste_match"] is None


def test_verify_draft_picks_the_most_similar_example_as_match():
    """여러 예시가 동시에 임계값을 넘으면, 재지시문에 들어갈 예시는
    가장 많이 겹친 쪽이어야 한다."""
    close = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")
    closer = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다!")
    state = {
        "draft": "불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다!",
        "tone_overridden": False, "examples": [close, closer], "display_name": "",
    }

    result = verify_draft_node(state)

    assert result["copy_paste_match"] is closer


def test_verify_draft_ignores_shared_display_name_prefix():
    """같은 브랜드명 인사말로 시작하는 두 답글이 본문은 완전히 달라도
    display_name을 지운 뒤에는 임계값을 넘으면 안 된다(최종 리뷰 실측
    0.82~0.94 과민 사례). display_name을 안 지운 원문 비교로는 여전히
    0.8을 넘는다는 것도 함께 확인한다."""
    display_name = "치밥대장"
    example = _golden("안녕하세요 치밥대장입니다. 맛있게 드셔주셔서 감사합니다.")
    state = {
        "draft": "안녕하세요 치밥대장입니다. 좋은 평가 남겨주셔서 감사합니다.",
        "tone_overridden": False, "examples": [example], "display_name": display_name,
    }

    # display_name을 안 지우면 공통 접두부만으로 임계값을 넘는다는 전제 확인.
    raw_ratio = difflib.SequenceMatcher(None, state["draft"], example.reply_text).ratio()
    assert raw_ratio >= 0.8

    result = verify_draft_node(state)

    assert result["violations"] == []
    assert result["copy_paste_match"] is None


def test_verify_draft_flags_copy_paste_violation_even_after_stripping_display_name():
    """진짜 복붙 사례는 display_name을 지워도 여전히 걸려야 한다 —
    display_name 제거가 진짜 위반까지 가려버리면 안 된다."""
    display_name = "치밥대장"
    example = _golden("치밥대장입니다, 불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")
    state = {
        "draft": "치밥대장입니다, 불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.",
        "tone_overridden": False, "examples": [example], "display_name": display_name,
    }

    result = verify_draft_node(state)

    assert "copy_paste" in result["violations"]
    assert result["copy_paste_match"] is example


def test_verify_draft_no_violations_when_clean():
    state = {"draft": "완전히 다른 새 문장입니다.", "tone_overridden": False, "examples": [], "display_name": ""}

    result = verify_draft_node(state)

    assert result["violations"] == []


def test_verify_draft_makes_no_llm_call(monkeypatch):
    """스펙 2.1절 — 검증은 전부 결정론적이고 LLM 자기비판을 쓰지 않는다
    (말투가 무난하게 수렴하는 위험 때문에 기각된 접근)."""
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("verify_draft_node는 LLM을 호출하면 안 된다")

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fail_if_called)
    example = _golden("불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.")

    # 위반이 있는 경우와 없는 경우 둘 다 — 어느 분기에서도 호출이 없어야 한다.
    verify_draft_node({"draft": "죄송합니다 😊", "tone_overridden": True, "examples": [example], "display_name": ""})
    verify_draft_node({"draft": "완전히 다른 새 문장입니다.", "tone_overridden": False, "examples": [example], "display_name": ""})
    verify_draft_node({
        "draft": "불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.",
        "tone_overridden": True, "examples": [example], "display_name": "",
    })


# ---------------------------------------------------------------------------
# fix_draft_node
# ---------------------------------------------------------------------------


def test_fix_draft_strips_emoji_without_llm_call(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("이모지만 위반이면 LLM을 호출하면 안 된다")

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fail_if_called)
    state = _draft_state(draft="감사합니다 😊", violations=["emoji"], tone_overridden=True)

    result = fix_draft_node(state)

    assert result["draft"] == "감사합니다"
    assert result["retry_count"] == 1


def test_fix_draft_regenerates_with_targeted_instruction_for_copy_paste(monkeypatch):
    captured = {}

    def _fake_call(system, user, max_tokens):
        captured["system"], captured["user"] = system, user
        return "새로 생성된 답글"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)
    example = _golden("예시 답글 원문")
    state = _draft_state(
        draft="예시 답글 원문", violations=["copy_paste"],
        copy_paste_match=example, examples=[example],
    )

    result = fix_draft_node(state)

    # 막연한 "다시 해봐"가 아니라 어떤 예시와 겹쳤는지 콕 집어 지시해야 한다.
    assert "예시 답글 원문" in captured["user"]
    assert result["draft"] == "새로 생성된 답글"
    assert result["retry_count"] == 1
    # 지목한 예시가 유일한 예시였으면 few-shot 목록은 비는 게 정상이다 —
    # 그라운딩할 다른 예시가 실제로 없는 상태이므로 특별 취급하지 않는다.
    assert "(아직 참고할 예시가 없습니다.)" in captured["system"]


def test_fix_draft_drops_matched_example_from_few_shot_but_keeps_others(monkeypatch):
    """복붙 재생성 프롬프트는 "이 문장 쓰지 마라"는 지시와 "이 문장이 좋은
    예시다"라는 데모를 동시에 들고 가면 안 된다 — 이 프로젝트는 이모지
    작업에서 이미 텍스트 지시만으로는 few-shot 데모를 못 이긴다는 걸
    실측했고, 예시 쪽에서 모순 신호를 지우는 방식으로 해결했다. 겹친 예시
    하나만 빼고 나머지 예시는 말투 그라운딩용으로 남아야 한다."""
    captured = {}

    def _fake_call(system, user, max_tokens):
        captured["system"], captured["user"] = system, user
        return "새로 생성된 답글"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)
    matched = _golden("겹친 예시 원문", id=1)
    other = _golden("관계없는 다른 예시 원문", id=2)
    state = _draft_state(
        draft="겹친 예시 원문", violations=["copy_paste"],
        copy_paste_match=matched, examples=[matched, other],
    )

    result = fix_draft_node(state)

    # 겹친 예시는 few-shot 블록(시스템 프롬프트)에서 빠졌지만,
    assert "겹친 예시 원문" not in captured["system"]
    # 유저 메시지의 "이것만 피해라" 지시문에는 그대로 들어가야 한다.
    assert "겹친 예시 원문" in captured["user"]
    # 나머지 예시는 말투 그라운딩용으로 살아있어야 한다.
    assert "관계없는 다른 예시 원문" in captured["system"]
    assert result["draft"] == "새로 생성된 답글"
    assert result["retry_count"] == 1


def test_fix_draft_does_not_mutate_examples_in_returned_state(monkeypatch):
    """few-shot에서 빼는 건 재생성 호출 한 번에 한정된다 — 그래프 state의
    examples를 영구히 줄여버리면 fix_draft 뒤 다시 도는 verify_draft가 그
    예시와의 복붙을 더는 못 잡는다."""
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "새로 생성된 답글",
    )
    matched = _golden("겹친 예시 원문")
    state = _draft_state(
        draft="겹친 예시 원문", violations=["copy_paste"],
        copy_paste_match=matched, examples=[matched],
    )

    result = fix_draft_node(state)

    assert "examples" not in result  # state의 examples를 덮어쓰지 않는다
    assert state["examples"] == [matched]  # 넘겨받은 리스트 자체도 그대로


def test_fix_draft_strips_emoji_from_regenerated_draft_when_tone_overridden(monkeypatch):
    """복붙 재생성 결과에도 이모지 제거를 다시 적용해야 한다 — 불만 리뷰에
    이모지가 섞이면 안 된다는 보장이 재시도 경로에서 깨지면 안 된다."""
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "새 답글입니다 😊",
    )
    example = _golden("예시 답글 원문")
    state = _draft_state(
        draft="예시 답글 원문", violations=["copy_paste", "emoji"],
        copy_paste_match=example, examples=[example], tone_overridden=True,
    )

    result = fix_draft_node(state)

    assert result["draft"] == "새 답글입니다"


def test_fix_draft_falls_back_to_emoji_strip_when_copy_paste_match_missing(monkeypatch):
    """방어 코드 — copy_paste 위반인데 match 객체가 없으면 지시문을 만들 수
    없다. 그 상태로 LLM을 부르는 대신(막연한 재시도 금지) 결정론적 정리만
    하고 재시도 횟수를 올린다."""
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("지목할 예시가 없으면 재생성하지 않는다")

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fail_if_called)
    state = _draft_state(
        draft="답글 😊", violations=["copy_paste"], copy_paste_match=None, tone_overridden=True,
    )

    result = fix_draft_node(state)

    assert result["draft"] == "답글"
    assert result["retry_count"] == 1


# ---------------------------------------------------------------------------
# route_after_verify — 재시도 상한
# ---------------------------------------------------------------------------


def test_route_after_verify_goes_to_finalize_when_clean():
    assert route_after_verify({"violations": [], "retry_count": 0}) == "finalize"


def test_route_after_verify_goes_to_fix_draft_when_violations_and_retries_left():
    assert route_after_verify({"violations": ["emoji"], "retry_count": 0}) == "fix_draft"
    assert route_after_verify({"violations": ["emoji"], "retry_count": 1}) == "fix_draft"


def test_route_after_verify_gives_up_after_max_retries():
    """off-by-one 경계 — retry_count가 2(=_MAX_RETRIES)에 도달하면 위반이
    남아 있어도 더 돌리지 않는다."""
    assert route_after_verify({"violations": ["copy_paste"], "retry_count": 2}) == "finalize"
    assert route_after_verify({"violations": ["copy_paste"], "retry_count": 3}) == "finalize"


# ---------------------------------------------------------------------------
# run_agent — 그래프 전체
# ---------------------------------------------------------------------------


def _make_review(db_session, store, platforms, **overrides):
    review = Review(
        store_id=store.id, platform_id=platforms["baemin"].id, menu_summary="치킨",
        rating=5, content="맛있어요", customer_nickname="손님", category="no_issue",
        created_at=datetime.now(timezone.utc), **overrides,
    )
    db_session.add(review)
    db_session.commit()
    return review


def test_run_agent_end_to_end_clean_draft(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "완전히 새로운 답글 문장입니다",
    )
    store = seeded_user["store"]
    review = _make_review(db_session, store, platforms)

    result = run_agent(db_session, review, store, reply_styles)

    assert result.passed_verification is True
    assert result.retry_count == 0
    assert result.content == "완전히 새로운 답글 문장입니다"


def test_run_agent_recovers_after_one_retry(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """첫 초안이 예시 복붙이면 한 번 재생성하고, 두 번째가 깨끗하면 통과로
    끝난다 — retry_count는 1."""
    store = seeded_user["store"]
    db_session.add(GoldenExample(
        store_id=store.id, category="no_issue", review_text="r", reply_text="항상 똑같은 답글",
        is_manual=True, is_synthetic=False, source="organic",
        created_at=datetime.now(timezone.utc),
    ))
    review = _make_review(db_session, store, platforms)

    calls = {"n": 0}

    def _fake_call(system, user, max_tokens):
        calls["n"] += 1
        return "항상 똑같은 답글" if calls["n"] == 1 else "오늘도 찾아주셔서 고맙습니다"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)

    result = run_agent(db_session, review, store, reply_styles)

    assert result.passed_verification is True
    assert result.retry_count == 1
    assert result.content == "오늘도 찾아주셔서 고맙습니다"
    assert calls["n"] == 2  # 초안 1회 + 재생성 1회


def test_run_agent_holds_after_max_retries_when_persistently_violating(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """절대 안 고쳐지는 모델 — 무한 루프에 빠지지도, 예외를 던지지도 않고
    retry_count=2에서 멈춰 passed_verification=False로 보고해야 한다."""
    store = seeded_user["store"]
    db_session.add(GoldenExample(
        store_id=store.id, category="no_issue", review_text="r", reply_text="항상 똑같은 답글",
        is_manual=True, is_synthetic=False, source="organic",
        created_at=datetime.now(timezone.utc),
    ))
    review = _make_review(db_session, store, platforms)

    calls = {"n": 0}

    def _fake_call(system, user, max_tokens):
        calls["n"] += 1
        return "항상 똑같은 답글"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)

    result = run_agent(db_session, review, store, reply_styles)

    assert result.passed_verification is False
    assert result.retry_count == 2  # 3이 아니다 — 상한이 2회 재시도
    assert result.content == "항상 똑같은 답글"
    # 초안 1회 + 재생성 2회 = 3회. 그 이상 호출되면 상한이 새는 것이다.
    assert calls["n"] == 3


def test_run_agent_does_not_hit_langgraph_recursion_limit(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """위 '절대 안 고쳐지는' 경로가 LangGraph의 recursion_limit(기본 25)에
    걸려 GraphRecursionError로 터지는 게 아니라, 우리 라우터가 스스로
    멈춰서 정상 종료하는지 확인한다."""
    from langgraph.errors import GraphRecursionError

    store = seeded_user["store"]
    db_session.add(GoldenExample(
        store_id=store.id, category="no_issue", review_text="r", reply_text="항상 똑같은 답글",
        is_manual=True, is_synthetic=False, source="organic",
        created_at=datetime.now(timezone.utc),
    ))
    review = _make_review(db_session, store, platforms)
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "항상 똑같은 답글",
    )

    try:
        result = run_agent(db_session, review, store, reply_styles)
    except GraphRecursionError as exc:  # pragma: no cover - 회귀 시에만 실행
        pytest.fail(f"재시도 루프가 스스로 멈추지 않았다: {exc}")

    assert result.passed_verification is False


def test_run_agent_propagates_node_exceptions_unwrapped(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """run_agent은 노드 내부 예외를 감싸지 않고 그대로 전파해야 한다 —
    reviews.py의 수동 생성 경로(503 처리)와 review_sync.py의 except
    Exception 둘 다 이 동작(원래 예외 타입이 그대로 올라옴)에 의존한다.
    그래프는 모듈 import 시 한 번만 컴파일되어 retrieve_memory_node를 직접
    참조하므로, 그 심볼 자체를 monkeypatch해도 이미 컴파일된 그래프에는
    영향이 없다 — 대신 retrieve_memory_node 내부에서 호출 시점에 전역
    이름으로 조회되는 fetch_golden_examples를 터뜨린다."""
    def _boom(*args, **kwargs):
        raise RuntimeError("retrieve_memory 실패")

    monkeypatch.setattr("app.llm.agent_graph.fetch_golden_examples", _boom)
    store = seeded_user["store"]
    review = _make_review(db_session, store, platforms)

    with pytest.raises(RuntimeError, match="retrieve_memory 실패"):
        run_agent(db_session, review, store, reply_styles)


def test_run_agent_returns_valid_trace_id(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """run_agent의 반환값에 trace_id가 항상 채워져 있고, 유효한 UUID
    문자열이어야 한다 — 이후 호출부가 이걸 ReviewReply/OnboardingScenario에
    저장해두고 나중에 LangSmith feedback을 그 trace에 붙이는 데 쓴다.
    call_sonnet_via_langgraph를 몽키패치하는 이유: 이 테스트는 전체
    run_agent을 실제로 끝까지 돌리므로(다른 노드를 미리 터뜨리는 식으로
    짧게 끝내지 않는다), 몽키패치 없이 두면 conftest의 _no_anthropic_key가
    지워둔 ANTHROPIC_API_KEY 때문에 KeyError로 죽는다(이 파일의
    test_run_agent_does_not_hit_langgraph_recursion_limit이 쓰는 것과
    동일한 패턴)."""
    store = seeded_user["store"]
    review = _make_review(db_session, store, platforms)
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "감사합니다",
    )

    result = run_agent(db_session, review, store, reply_styles)

    assert isinstance(result.trace_id, str)
    uuid.UUID(result.trace_id)  # ValueError를 던지지 않으면 유효한 UUID


def test_run_agent_gives_each_call_a_different_trace_id(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """같은 리뷰로 run_agent을 두 번 불러도 trace_id가 겹치면 안 된다 —
    겹치면 서로 다른 생성 시도의 feedback이 같은 trace에 뒤섞인다."""
    store = seeded_user["store"]
    review = _make_review(db_session, store, platforms)
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "감사합니다",
    )

    first = run_agent(db_session, review, store, reply_styles)
    second = run_agent(db_session, review, store, reply_styles)

    assert first.trace_id != second.trace_id
