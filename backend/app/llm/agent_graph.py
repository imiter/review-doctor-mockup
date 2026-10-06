"""리뷰 답글 생성을 LangGraph 상태 그래프로 표현한다(스펙
2026-10-03-ai-agent-llmops-reply-design.md 2절). 기존 generate_ai_reply
(app/llm/generate.py)가 하던 일을 노드 5개로 쪼갠다 — retrieve_memory
(기억 조회) → generate_draft(초안 생성) → verify_draft(결정론적 검증)
→ [위반 시] fix_draft(수정) → finalize(최종 반환). LLM 재판단(자기비판)은
쓰지 않는다 — 말투가 무난하게 수렴할 위험이 있다고 판단해 기각함(스펙
2.1절), 검증은 전부 결정론적 체크로만 한다.

이 파일은 그중 retrieve_memory/generate_draft 두 노드만 만든다(Task 2) —
verify_draft/fix_draft/finalize와 StateGraph 조립, run_agent 진입점은
Task 3에서 추가한다. generate.py의 generate_ai_reply 자체는 이 작업에서
건드리지 않는다 — generate_ai_reply를 이 그래프 호출로 교체하는 것은
Task 4다."""

from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.generate import (
    CATEGORY_LABELS,
    _FALLBACK_STYLE_RULES,
    _build_system_prompt,
    _build_user_message,
    _find_menu_context,
    _resolve_display_name,
    _strip_emoji,
    fetch_active_rules,
)
from app.llm.langchain_client import call_sonnet_via_langgraph
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, ReplyStyle, Review, Store, StoreStyleProfile


class AgentState(TypedDict, total=False):
    # 입력(그래프 시작 시 채움)
    db: Session
    review: Review
    store: Store
    style: ReplyStyle
    # retrieve_memory가 채움
    rules: dict[str, str]
    style_rules: str
    examples: list[GoldenExample]
    repeat_count: int
    category_label: str
    tone_instruction: str
    tone_overridden: bool
    display_name: str
    menu_context: str | None
    # generate_draft/fix_draft가 채움
    draft: str
    # verify_draft가 채움
    violations: list[str]
    copy_paste_match: GoldenExample | None
    # 루프 제어
    retry_count: int
    # finalize가 채움
    final_content: str
    passed_verification: bool


def retrieve_memory_node(state: AgentState) -> dict:
    """기존 generate_ai_reply의 앞부분(app/llm/generate.py의
    generate_ai_reply 중 시스템/유저 프롬프트 빌드 전까지)을 그대로 옮긴
    것 — 절차/의미/일화 기억을 전부 조회해 state에 채운다."""
    db, review, store, style = state["db"], state["review"], state["store"], state["style"]

    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES
    rules = fetch_active_rules(db)

    examples = fetch_golden_examples(db, store.id, review.category, review.content, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = CATEGORY_LABELS.get(review.category, review.category)

    tone_overridden = review.category != "no_issue" or review.is_sensitive or review.sentiment_conflict
    tone_instruction = rules["complaint_tone_override"] if tone_overridden else style.tone_instruction
    if review.category == "delivery":
        tone_instruction = f"{tone_instruction}\n\n{rules['delivery_boundary']}"

    display_name = _resolve_display_name(db, store, review)
    menu_context = _find_menu_context(db, store, review)

    return {
        "rules": rules, "style_rules": style_rules, "examples": examples,
        "repeat_count": repeat_count, "category_label": category_label,
        "tone_instruction": tone_instruction, "tone_overridden": tone_overridden,
        "display_name": display_name, "menu_context": menu_context,
        "retry_count": 0,
    }


def generate_draft_node(state: AgentState, *, extra_instruction: str | None = None) -> dict:
    """기존 generate_ai_reply의 뒷부분(시스템/유저 프롬프트 빌드 →
    call_sonnet → 이모지 스트립)을 옮긴 것. extra_instruction은
    fix_draft가 복붙 위반을 좁게 재지시할 때만 채워 넣는다(Task 3에서
    사용) — 평소 generate_draft 호출(초안 1회차)에서는 None."""
    system_prompt = _build_system_prompt(
        state["display_name"], state["style_rules"], state["examples"],
        state["tone_instruction"], state["rules"], state["menu_context"],
        strip_example_emoji=state["tone_overridden"],
    )
    user_message = _build_user_message(
        state["review"], state["category_label"], state["repeat_count"], state["rules"],
    )
    if extra_instruction:
        user_message = f"{user_message}\n\n{extra_instruction}"

    content = call_sonnet_via_langgraph(system_prompt, user_message, max_tokens=800)
    draft = _strip_emoji(content) if state["tone_overridden"] else content
    return {"draft": draft}
