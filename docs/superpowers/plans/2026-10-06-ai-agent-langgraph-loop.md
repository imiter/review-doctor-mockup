# 리뷰 답글 에이전트 — LangGraph 루프 (Plan 2/4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-10-03-ai-agent-llmops-reply-design.md` 2절(에이전트 루프/추론 설계)을 구현한다 — 지금 `generate_ai_reply` 단일 함수로 돼 있는 흐름을 LangGraph 상태 그래프(retrieve_memory→generate_draft→verify_draft→fix_draft→finalize)로 재구성하고, 결정론적 검증(이모지 규칙·few-shot 복붙 유사도)과 재시도(최대 2회)·보류 메커니즘을 추가한다.

**Architecture:** 기존 `backend/app/llm/generate.py`의 프롬프트 조립 로직(규칙 조회, few-shot 조회, 메뉴/공지 그라운딩, 프롬프트 빌더)은 그대로 재사용하고, 새 모듈 `backend/app/llm/agent_graph.py`에 LangGraph `StateGraph`로 감싼다. LLM 호출은 `langchain-anthropic`의 `ChatAnthropic`으로 교체한다(raw Anthropic SDK는 `classify.py`/`onboarding.py`의 Haiku 호출에 계속 쓰이므로 손대지 않는다 — 이번 플랜은 답글 생성 경로만 범위). 보류 시 초안은 새 테이블 없이 기존 `ReviewReply(reply_type="ai_draft")` + `review.status="pending"` 메커니즘을 재사용한다.

**Tech Stack:** `langchain-anthropic`, `langgraph` (신규 추가), 기존 FastAPI/SQLAlchemy/pytest.

## Global Constraints

- **자동답글 대상 범위는 이번에 넓히지 않는다** — 사용자가 2026-10-06 명시적으로 단계적 적용을 요청함(새 루프를 충분히 검증하기 전에 범위부터 넓히는 건 불안 요소가 크다는 판단). `review_sync.py`의 기존 게이트 `rating≥5 AND category=="no_issue" AND not is_sensitive AND not sentiment_conflict` 4개 조건을 **전부 그대로 유지**한다. 이번 플랜은 그 좁은 범위 안에서 검증+재시도+보류 메커니즘만 새로 넣는다(스펙 2.1절 "적용 범위 확대" 절 참고 — 2026-10-05 결정이 2026-10-06에 단계적 적용으로 수정됨).
- 재시도 한도는 **최대 2회**(총 3번 생성 시도)다.
- `verify_draft`는 **결정론적 체크만** 한다 — LLM에게 "이 답글 괜찮아?"라고 재판단시키지 않는다(북극성 목표인 "진짜 사장님 말투" 보존을 위해, LLM 재판단을 거칠수록 말투가 무난하게 수렴할 위험이 있다고 판단해 명시적으로 기각함 — 스펙 2.1절).
- 이모지 위반은 LLM 호출 없이 코드로 바로 고친다(`_strip_emoji` 재사용). 복붙 위반만 LLM 재생성을 쓴다.
- 보류된 리뷰는 **새 테이블/컬럼을 만들지 않는다** — 기존 `ReviewReply(reply_type="ai_draft")` + `review.status="pending"` 메커니즘(`backend/app/routers/reviews.py`의 `generate_reply`가 이미 쓰는 패턴)을 그대로 재사용한다.
- `generate_ai_reply(db, review, store, style) -> str`의 기존 공개 시그니처는 바뀌지 않는다 — `reviews.py`/`reply_onboarding.py`의 기존 호출부는 수정하지 않는다. 검증 결과(통과/보류)가 필요한 건 `review_sync.py`뿐이라, 거기서만 더 풍부한 반환값을 주는 별도 함수(`run_agent`)를 직접 쓴다.
- `langgraph`/`langchain-anthropic`은 버전에 따라 API가 바뀔 수 있다 — 아래 태스크의 코드 스니펫은 참고 구조이지 반드시 그대로 동작한다는 보장이 아니다. 실제 구현 전에 **설치된 패키지의 실제 시그니처를 직접 확인**할 것(`pip show langgraph langchain-anthropic`, `python -c "from langgraph.graph import StateGraph; help(StateGraph)"`, `python -c "from langchain_anthropic import ChatAnthropic; help(ChatAnthropic)"` 등) — 추측으로 쓰지 말 것.

---

### Task 1: 의존성 추가 + ChatAnthropic 얇은 래퍼

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/llm/langchain_client.py`
- Test: `backend/tests/test_llm_langchain_client.py`

**Interfaces:**
- Produces: `call_sonnet_via_langgraph(system: str, user: str, *, max_tokens: int = 1000) -> str` — `app/llm/client.py`의 `call_sonnet`과 같은 모양(system+user 문자열 받아 텍스트 반환)이지만 내부적으로 `ChatAnthropic`을 쓴다. 테스트에서 이 함수 하나만 monkeypatch하면 실제 API 호출 없이 에이전트 그래프 전체를 검증할 수 있다 — 기존 `client.call_sonnet`/`call_haiku`를 테스트에서 monkeypatch하는 것과 동일한 패턴.

- [ ] **Step 1: 설치된 패키지 실제 시그니처 확인**

```bash
cd backend && pip install langchain-anthropic langgraph
python -c "from langchain_anthropic import ChatAnthropic; import inspect; print(inspect.signature(ChatAnthropic.__init__))"
python -c "from langchain_core.messages import SystemMessage, HumanMessage; print('ok')"
```

출력된 실제 파라미터 이름(모델명 인자가 `model`인지 `model_name`인지, API 키 인자 이름, timeout 인자 이름 등)을 확인하고 Step 3에서 그대로 쓸 것 — 아래 코드는 구조 참고용이다.

- [ ] **Step 2: requirements.txt 수정**

`anthropic` 줄 바로 아래에 추가:

```
langchain-anthropic
langgraph
```

- [ ] **Step 3: 래퍼 작성**

```python
"""LangGraph 에이전트 루프 전용 LLM 호출 래퍼. backend/app/llm/client.py의
call_sonnet과 똑같은 "system+user 문자열 → 텍스트" 모양을 유지해 테스트에서
이 함수 하나만 monkeypatch하면 된다(기존 client.call_sonnet 테스트 패턴과
동일). classify.py/onboarding.py의 Haiku 호출은 범위 밖이라 client.py의
raw Anthropic SDK 경로를 그대로 쓴다 — 이 파일은 에이전트 루프(Sonnet
답글 생성)에만 쓴다.

Step 1에서 확인한 ChatAnthropic의 실제 생성자 시그니처에 맞춰 아래
_client() 함수의 키워드 인자 이름을 맞출 것."""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

SONNET_MODEL = "claude-sonnet-5"


def _client(max_tokens: int) -> ChatAnthropic:
    # timeout=60.0 — client.py의 call_sonnet과 동일한 이유(API 저하 시
    # 요청 하나가 수십 분 걸리는 것을 막음).
    return ChatAnthropic(
        model=SONNET_MODEL, api_key=os.environ["ANTHROPIC_API_KEY"],
        max_tokens=max_tokens, timeout=60.0,
        thinking={"type": "disabled"},
    )


def call_sonnet_via_langgraph(system: str, user: str, *, max_tokens: int = 1000) -> str:
    response = _client(max_tokens).invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])
    return response.content
```

(`response.content`가 문자열이 아니라 블록 리스트로 올 수도 있다 — Step 1에서 실제 호출 결과 타입을 확인 못 했다면, 방어적으로 `response.content if isinstance(response.content, str) else response.content[0]["text"]` 같은 분기를 넣을 것. 실제로 어느 쪽인지는 설치된 버전에 달려있으므로 확정하지 말고 직접 확인한다.)

- [ ] **Step 4: 테스트 작성 — 구조만 검증(실제 API 호출 없이)**

```python
from unittest.mock import MagicMock, patch

from app.llm.langchain_client import call_sonnet_via_langgraph


def test_call_sonnet_via_langgraph_returns_text_content(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.content = "테스트 응답"

    with patch("app.llm.langchain_client.ChatAnthropic") as MockChatAnthropic:
        MockChatAnthropic.return_value.invoke.return_value = fake_response
        result = call_sonnet_via_langgraph("시스템 프롬프트", "사용자 메시지")

    assert result == "테스트 응답"
    MockChatAnthropic.return_value.invoke.assert_called_once()
```

- [ ] **Step 5: 테스트 실행 확인**

Run: `cd backend && python -m pytest tests/test_llm_langchain_client.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/llm/langchain_client.py backend/tests/test_llm_langchain_client.py
git commit -m "feat: langchain-anthropic 기반 Sonnet 호출 래퍼 추가"
```

---

### Task 2: `agent_graph.py` — State + retrieve_memory/generate_draft 노드

**Files:**
- Create: `backend/app/llm/agent_graph.py`
- Test: `backend/tests/test_llm_agent_graph.py`

**Interfaces:**
- Consumes: `fetch_active_rules`/`_resolve_display_name`/`_find_menu_context`/`_build_system_prompt`/`_build_user_message`/`CATEGORY_LABELS`/`_FALLBACK_STYLE_RULES`(모두 `backend/app/llm/generate.py`, 기존 그대로), `fetch_golden_examples`/`count_recent_same_category`(`backend/app/llm/rag.py`, 기존 그대로), `call_sonnet_via_langgraph`(Task 1).
- Produces: `AgentState`(TypedDict), `retrieve_memory_node(state) -> dict`, `generate_draft_node(state) -> dict` — 이번 태스크에서는 이 두 노드만 만들고 아직 그래프로 엮지 않는다(Task 3에서 전체 그래프 조립).

이번 태스크는 기존 `generate_ai_reply`(`backend/app/llm/generate.py:297-319`)의 본문을 그대로 두 노드로 쪼개는 작업이다 — 로직을 새로 짜는 게 아니라 옮기는 것이다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime, timezone
from unittest.mock import patch

from app.llm.agent_graph import generate_draft_node, retrieve_memory_node
from app.models import Review


def test_retrieve_memory_node_populates_state(db_session, seeded_user, reply_styles):
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=1, menu_summary="치킨", rating=5,
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_agent_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.agent_graph'`

- [ ] **Step 3: agent_graph.py 작성 (State + 두 노드)**

```python
"""리뷰 답글 생성을 LangGraph 상태 그래프로 표현한다(스펙
2026-10-03-ai-agent-llmops-reply-design.md 2절). 기존 generate_ai_reply
(app/llm/generate.py)가 하던 일을 노드 5개로 쪼갠다 — retrieve_memory
(기억 조회) → generate_draft(초안 생성) → verify_draft(결정론적 검증)
→ [위반 시] fix_draft(수정) → finalize(최종 반환). LLM 재판단(자기비판)은
쓰지 않는다 — 말투가 무난하게 수렴할 위험이 있다고 판단해 기각함(스펙
2.1절), 검증은 전부 결정론적 체크로만 한다."""

from typing import TypedDict

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
from app.models import GoldenExample, Review, ReplyStyle, Store, StoreStyleProfile
from sqlalchemy import select


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
    """기존 generate_ai_reply의 앞부분(app/llm/generate.py:298-312)을
    그대로 옮긴 것 — 절차/의미/일화 기억을 전부 조회해 state에 채운다."""
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
    """기존 generate_ai_reply의 뒷부분(app/llm/generate.py:313-319)을
    옮긴 것. extra_instruction은 fix_draft가 복붙 위반을 좁게 재지시할
    때만 채워 넣는다(Task 3에서 사용) — 평소 generate_draft 호출(초안
    1회차)에서는 None."""
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_agent_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/agent_graph.py backend/tests/test_llm_agent_graph.py
git commit -m "feat: agent_graph.py — retrieve_memory/generate_draft 노드 (기존 로직 이관)"
```

---

### Task 3: verify_draft + fix_draft 노드 + 그래프 조립

**Files:**
- Modify: `backend/app/llm/agent_graph.py`
- Test: `backend/tests/test_llm_agent_graph.py`

**Interfaces:**
- Consumes: Task 2의 `AgentState`/`retrieve_memory_node`/`generate_draft_node`.
- Produces: `verify_draft_node(state) -> dict`, `fix_draft_node(state) -> dict`, `route_after_verify(state) -> str`, 컴파일된 그래프 `_GRAPH`, `AgentResult`(dataclass: `content: str`, `passed_verification: bool`, `retry_count: int`), `run_agent(db: Session, review: Review, store: Store, style: ReplyStyle) -> AgentResult`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_verify_draft_flags_emoji_violation_when_tone_overridden():
    from app.llm.agent_graph import verify_draft_node

    state = {"draft": "죄송합니다 😊", "tone_overridden": True, "examples": []}
    result = verify_draft_node(state)

    assert "emoji" in result["violations"]


def test_verify_draft_flags_copy_paste_violation():
    from app.llm.agent_graph import verify_draft_node
    from app.models import GoldenExample

    example = GoldenExample(id=1, store_id=1, category="food_quality", review_text="맛없어요", reply_text="불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.", is_manual=True, is_synthetic=False, source="organic")
    state = {
        "draft": "불편을 드려 정말 죄송합니다. 다음엔 더 신경쓰겠습니다.",
        "tone_overridden": False, "examples": [example],
    }

    result = verify_draft_node(state)

    assert "copy_paste" in result["violations"]
    assert result["copy_paste_match"] is example


def test_verify_draft_no_violations_when_clean():
    from app.llm.agent_graph import verify_draft_node

    state = {"draft": "완전히 다른 새 문장입니다.", "tone_overridden": False, "examples": []}
    result = verify_draft_node(state)

    assert result["violations"] == []


def test_fix_draft_strips_emoji_without_llm_call(monkeypatch):
    from app.llm.agent_graph import fix_draft_node

    def _fail_if_called(*a, **kw):
        raise AssertionError("이모지만 위반이면 LLM을 호출하면 안 된다")

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fail_if_called)
    state = {"draft": "감사합니다 😊", "violations": ["emoji"], "copy_paste_match": None, "retry_count": 0}

    result = fix_draft_node(state)

    assert result["draft"] == "감사합니다"
    assert result["retry_count"] == 1


def test_fix_draft_regenerates_with_targeted_instruction_for_copy_paste(monkeypatch):
    from app.llm.agent_graph import fix_draft_node
    from app.models import GoldenExample

    captured = {}

    def _fake_call(system, user, max_tokens):
        captured["user"] = user
        return "새로 생성된 답글"

    monkeypatch.setattr("app.llm.agent_graph.call_sonnet_via_langgraph", _fake_call)
    example = GoldenExample(id=1, store_id=1, category="food_quality", review_text="r", reply_text="예시 답글 원문", is_manual=True, is_synthetic=False, source="organic")
    state = {
        "draft": "예시 답글 원문", "violations": ["copy_paste"], "copy_paste_match": example,
        "retry_count": 0, "display_name": "치킨대장", "style_rules": "s", "examples": [example],
        "tone_instruction": "t", "rules": {"menu_grounding": "m", "few_shot_anti_overfit": "f", "no_issue_framing": "n"},
        "menu_context": None, "tone_overridden": False,
        "review": type("R", (), {"rating": 5, "category": "no_issue", "content": "c", "customer_order_count": 1, "is_sensitive": False})(),
        "category_label": "무난", "repeat_count": 0,
    }

    result = fix_draft_node(state)

    assert "예시 답글 원문" in captured["user"]  # 어떤 예시와 겹쳤는지 지시문에 포함
    assert result["draft"] == "새로 생성된 답글"
    assert result["retry_count"] == 1


def test_route_after_verify_goes_to_finalize_when_clean():
    from app.llm.agent_graph import route_after_verify

    assert route_after_verify({"violations": [], "retry_count": 0}) == "finalize"


def test_route_after_verify_goes_to_fix_draft_when_violations_and_retries_left():
    from app.llm.agent_graph import route_after_verify

    assert route_after_verify({"violations": ["emoji"], "retry_count": 1}) == "fix_draft"


def test_route_after_verify_gives_up_after_max_retries():
    from app.llm.agent_graph import route_after_verify

    assert route_after_verify({"violations": ["copy_paste"], "retry_count": 2}) == "finalize"


def test_run_agent_end_to_end_clean_draft(db_session, seeded_user, reply_styles, monkeypatch):
    from app.llm.agent_graph import run_agent
    from app.models import Review
    from datetime import datetime, timezone

    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "완전히 새로운 답글 문장입니다",
    )
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=1, menu_summary="치킨", rating=5,
        content="맛있어요", customer_nickname="손님", category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    result = run_agent(db_session, review, store, reply_styles)

    assert result.passed_verification is True
    assert result.retry_count == 0
    assert result.content == "완전히 새로운 답글 문장입니다"


def test_run_agent_holds_after_max_retries_when_persistently_violating(db_session, seeded_user, reply_styles, monkeypatch):
    from app.llm.agent_graph import run_agent
    from app.models import GoldenExample, Review
    from datetime import datetime, timezone

    store = seeded_user["store"]
    db_session.add(GoldenExample(
        store_id=store.id, category="no_issue", review_text="r", reply_text="항상 똑같은 답글",
        is_manual=True, is_synthetic=False, source="organic", created_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=store.id, platform_id=1, menu_summary="치킨", rating=5,
        content="맛있어요", customer_nickname="손님", category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()
    # 매번 예시와 똑같은 문장만 내놓는 모델을 흉내낸다 — 재시도해도 안 고쳐짐
    monkeypatch.setattr(
        "app.llm.agent_graph.call_sonnet_via_langgraph",
        lambda system, user, max_tokens: "항상 똑같은 답글",
    )

    result = run_agent(db_session, review, store, reply_styles)

    assert result.passed_verification is False
    assert result.retry_count == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_agent_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'verify_draft_node'`

- [ ] **Step 3: 구현 추가 (agent_graph.py 이어서 작성)**

```python
import difflib

_MAX_RETRIES = 2
# difflib.SequenceMatcher 비율 — 1.0이면 완전히 동일. 실측 데이터로
# 보정 전이라 보수적으로 시작한다(스펙 1.4.1의 _CONSISTENCY_DISTANCE_THRESHOLD와
# 같은 종류의 잠정값 — 운영하면서 조정 대상).
_COPY_PASTE_SIMILARITY_THRESHOLD = 0.8


def verify_draft_node(state: AgentState) -> dict:
    """결정론적 체크 둘만 한다 — LLM 재판단 없음(스펙 2.1절). (a) 불만
    톤인데 이모지가 섞였는지, (b) few-shot 예시 중 하나를 사실상 그대로
    복붙했는지(SequenceMatcher 비율로 판단)."""
    draft = state["draft"]
    violations: list[str] = []
    copy_paste_match = None

    if state["tone_overridden"] and _EMOJI_PATTERN.search(draft):
        violations.append("emoji")

    best_ratio = 0.0
    for ex in state.get("examples", []):
        ratio = difflib.SequenceMatcher(None, draft, ex.reply_text).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            if ratio >= _COPY_PASTE_SIMILARITY_THRESHOLD:
                copy_paste_match = ex
    if copy_paste_match is not None:
        violations.append("copy_paste")

    return {"violations": violations, "copy_paste_match": copy_paste_match}


def fix_draft_node(state: AgentState) -> dict:
    """이모지만 위반이면 코드로 바로 제거(LLM 호출 없음). 복붙 위반이
    있으면(이모지와 동시에 있어도) 그 예시를 콕 집어 "그 부분만 바꿔라"는
    좁은 지시로 재생성한다 — strip_emoji는 재생성된 결과에도 다시
    적용한다(tone_overridden이면)."""
    violations = state["violations"]
    retry_count = state["retry_count"] + 1

    if "copy_paste" not in violations:
        # 이모지만 위반 — 결정론적으로 고친다.
        return {"draft": _strip_emoji(state["draft"]), "retry_count": retry_count}

    match = state["copy_paste_match"]
    extra_instruction = (
        f'방금 만든 답글이 다음 예시와 너무 비슷합니다: "{match.reply_text}". '
        "이 문장을 그대로 쓰지 말고, 같은 상황이지만 표현을 완전히 새로 바꿔서 다시 작성하세요."
    )
    patch = generate_draft_node(state, extra_instruction=extra_instruction)
    draft = patch["draft"]
    if state["tone_overridden"]:
        draft = _strip_emoji(draft)
    return {"draft": draft, "retry_count": retry_count}


def route_after_verify(state: AgentState) -> str:
    if not state["violations"]:
        return "finalize"
    if state["retry_count"] >= _MAX_RETRIES:
        return "finalize"
    return "fix_draft"


def finalize_node(state: AgentState) -> dict:
    return {
        "final_content": state["draft"],
        "passed_verification": not state["violations"],
    }


def _build_graph():
    from langgraph.graph import END, StateGraph

    graph = StateGraph(AgentState)
    graph.add_node("retrieve_memory", retrieve_memory_node)
    graph.add_node("generate_draft", generate_draft_node)
    graph.add_node("verify_draft", verify_draft_node)
    graph.add_node("fix_draft", fix_draft_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("retrieve_memory")
    graph.add_edge("retrieve_memory", "generate_draft")
    graph.add_edge("generate_draft", "verify_draft")
    graph.add_conditional_edges(
        "verify_draft", route_after_verify,
        {"fix_draft": "fix_draft", "finalize": "finalize"},
    )
    graph.add_edge("fix_draft", "verify_draft")
    graph.add_edge("finalize", END)
    return graph.compile()


_GRAPH = _build_graph()


from dataclasses import dataclass


@dataclass
class AgentResult:
    content: str
    passed_verification: bool
    retry_count: int


def run_agent(db: Session, review: Review, store: Store, style: ReplyStyle) -> AgentResult:
    final_state = _GRAPH.invoke({"db": db, "review": review, "store": store, "style": style})
    return AgentResult(
        content=final_state["final_content"],
        passed_verification=final_state["passed_verification"],
        retry_count=final_state["retry_count"],
    )
```

(`_EMOJI_PATTERN`은 Task 2에서 `generate.py`에서 import하지 않았다면 여기 import 목록에 추가할 것 — `from app.llm.generate import _EMOJI_PATTERN`. `dataclass`/`difflib` import는 파일 상단으로 옮겨 정리할 것 — 위에서는 단계 설명을 위해 중간에 썼다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_agent_graph.py -v`
Expected: PASS 전체

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/agent_graph.py backend/tests/test_llm_agent_graph.py
git commit -m "feat: agent_graph.py — verify_draft/fix_draft 노드 + 그래프 조립 + run_agent"
```

---

### Task 4: `generate_ai_reply`를 run_agent 위의 얇은 래퍼로 교체

**Files:**
- Modify: `backend/app/llm/generate.py:297-319`
- Test: `backend/tests/test_llm_generate.py`

**Interfaces:**
- Consumes: `run_agent`(Task 3).
- Produces: `generate_ai_reply(db, review, store, style) -> str` — 기존과 동일한 시그니처/반환 타입, 호출부(`reviews.py`/`reply_onboarding.py`) 무수정.

- [ ] **Step 1: 기존 테스트가 여전히 통과하는지 먼저 확인(회귀 기준선)**

Run: `cd backend && python -m pytest tests/test_llm_generate.py -v`
Expected: 현재 PASS(아직 안 건드림) — 이 결과를 Step 4와 비교한다.

- [ ] **Step 2: generate_ai_reply 본문 교체**

`backend/app/llm/generate.py`의 `generate_ai_reply` 함수 전체(297~319행)를 교체:

```python
def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    """app/llm/agent_graph.py의 LangGraph 루프를 돌리고 최종 텍스트만
    반환한다 — 기존 호출부(reviews.py의 수동 생성, reply_onboarding.py의
    훈련카드 초안)는 검증 통과 여부를 몰라도 되므로 공개 시그니처를
    그대로 유지한다. 검증 결과(통과/보류)가 필요한 review_sync.py의
    자동답글 경로는 이 함수가 아니라 run_agent를 직접 쓴다(Task 5)."""
    from app.llm.agent_graph import run_agent

    return run_agent(db, review, store, style).content
```

(지연 import로 둔다 — `agent_graph.py`가 `generate.py`의 헬퍼 함수들을 import하므로, 모듈 최상단에서 서로 import하면 순환 import가 된다.)

- [ ] **Step 3: 기존 테스트 중 깨지는 것 확인하고 고치기**

`client.call_sonnet`을 직접 monkeypatch하던 기존 테스트들은 이제 `agent_graph.call_sonnet_via_langgraph`를 monkeypatch해야 통과한다. `test_llm_generate.py`를 열어서 `client.call_sonnet`을 monkeypatch하는 테스트를 전부 찾아 `app.llm.agent_graph.call_sonnet_via_langgraph`로 대상을 바꾼다. 테스트가 검증하려는 내용(규칙 텍스트가 프롬프트에 들어가는지 등)은 그대로 두고, monkeypatch 대상 경로만 수정한다 — "Similar to Task N"으로 생략하지 말고 실제로 파일을 열어 하나씩 고칠 것.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_generate.py tests/test_llm_agent_graph.py -v`
Expected: PASS 전체

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/generate.py backend/tests/test_llm_generate.py
git commit -m "refactor: generate_ai_reply를 agent_graph.run_agent 위의 얇은 래퍼로 교체"
```

---

### Task 5: review_sync.py 자동답글 경로를 run_agent + 보류 메커니즘으로 교체

**Files:**
- Modify: `backend/app/review_sync.py:526-567` (신규 리뷰 경로), `:580-623` (소급 경로)
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: `run_agent`(Task 3), `AgentResult`.
- Produces: 변경 없음(이 태스크는 review_sync.py 내부 동작만 바꾼다).

**게이트 조건은 전혀 안 바뀐다** — `rating>=5 AND category=="no_issue" AND not is_sensitive AND not sentiment_conflict`를 그대로 유지한다(이번 플랜 Global Constraints 참고). 바뀌는 건 그 조건을 통과한 리뷰를 처리하는 방식뿐이다: `generate_ai_reply`+무조건 제출 → `run_agent`+검증 통과 시에만 제출, 실패 시 보류.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_sync_holds_review_as_ai_draft_when_verification_fails(db_session, seeded_user, platforms, monkeypatch):
    # 기존 test_review_sync.py의 자동답글 성공 케이스 테스트와 동일한
    # 셋업(reply_settings.auto_reply_enabled=True, Pro 구독, 새 5점
    # no_issue 리뷰가 fetch_all_reviews에서 반환되도록 monkeypatch)을
    # 그대로 가져다 쓰되, run_agent만 아래처럼 교체한다.
    from unittest.mock import MagicMock
    import app.review_sync as review_sync_mod

    fake_result = MagicMock(content="검증 실패한 초안", passed_verification=False, retry_count=2)
    monkeypatch.setattr(review_sync_mod, "run_agent", lambda db, review, store, style: fake_result)

    # ... (기존 테스트의 나머지 셋업 + _run_sync 호출 패턴을 그대로 따라감) ...

    from app.models import Review, ReviewReply

    review = db_session.query(Review).filter_by(rating=5, category="no_issue").first()
    assert review.status == "pending"  # 자동 제출 안 됨, 보류
    draft = db_session.query(ReviewReply).filter_by(review_id=review.id, reply_type="ai_draft").first()
    assert draft is not None
    assert draft.content == "검증 실패한 초안"
    # submit_reply가 호출되지 않았어야 한다(mock 호출 여부로 확인) — 기존
    # 테스트가 submit_reply를 어떻게 monkeypatch하는지 찾아서 같은 방식으로
    # assert_not_called() 확인할 것.


def test_sync_submits_normally_when_verification_passes(db_session, seeded_user, platforms, monkeypatch):
    from unittest.mock import MagicMock
    import app.review_sync as review_sync_mod

    fake_result = MagicMock(content="검증 통과한 답글", passed_verification=True, retry_count=0)
    monkeypatch.setattr(review_sync_mod, "run_agent", lambda db, review, store, style: fake_result)

    # ... 기존 자동답글 성공 테스트와 동일 셋업 ...

    from app.models import Review

    review = db_session.query(Review).filter_by(rating=5, category="no_issue").first()
    assert review.status == "answered"  # 기존과 동일하게 제출됨
```

(두 테스트 모두 `# ...` 부분은 생략한 게 아니라, `test_review_sync.py`에 이미 있는 "신규 리뷰 자동답글 성공" 테스트의 셋업 코드(매장 발견, `fetch_all_reviews` monkeypatch, `reply_settings`/`Subscription` 생성 등)를 그 파일을 직접 열어서 그대로 복사해 채워 넣으라는 뜻이다 — 파일이 매우 커서 플랜에 전체를 옮기지 않았다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_review_sync.py -k "holds_review_as_ai_draft or submits_normally" -v`
Expected: FAIL — `review.status`가 기대와 다름(아직 무조건 제출하는 기존 로직이라)

- [ ] **Step 3: review_sync.py 수정**

import 구간에 추가: `from app.llm.agent_graph import run_agent`(`generate_ai_reply` import는 더 이상 이 파일에서 안 쓰면 제거, 다른 곳에서 쓰고 있지 않은지 확인 후 제거).

신규 리뷰 경로(526~567행대)의 `elif` 블록 내부를 교체:

```python
                elif (
                    auto_reply_style is not None
                    and review.rating >= _AUTO_REPLY_MIN_RATING_FLOOR
                    and review.category == "no_issue"
                    and not review.is_sensitive
                    and not review.sentiment_conflict
                ):
                    try:
                        result = run_agent(db, review, store, auto_reply_style)
                        if result.passed_verification:
                            submit_reply(session.page, shop_no, review.external_review_id, result.content)
                            db.add(ReviewReply(
                                review_id=review.id, reply_type="final", style_id=auto_reply_style.id,
                                content=result.content, created_at=datetime.now(timezone.utc),
                            ))
                            review.status = "answered"
                            db.commit()
                        else:
                            # 재시도 2회에도 검증을 못 통과했다 — 배민에 제출하지
                            # 않고 보류한다. 기존 "AI 추천 답글" 화면이 이미 쓰는
                            # ai_draft 패턴을 그대로 재사용해서(reviews.py의
                            # generate_reply와 동일), 사장님이 백지가 아니라 이
                            # 실패한 초안을 고쳐서 등록할 수 있게 한다 — 새
                            # 테이블/컬럼 없음(스펙 2.1절 보류 정책).
                            db.add(ReviewReply(
                                review_id=review.id, reply_type="ai_draft", style_id=auto_reply_style.id,
                                content=result.content, created_at=datetime.now(timezone.utc),
                            ))
                            review.status = "pending"
                            db.commit()
                    except Exception as e:
                        auto_reply_errors.append(f"리뷰 {review.id}(별점 {review.rating}): {e}")
```

소급 경로(580~623행대)의 `for review in backlog:` 루프 내부도 동일하게 교체:

```python
                for review in backlog:
                    try:
                        result = run_agent(db, review, store, auto_reply_style)
                        if result.passed_verification:
                            submit_reply(session.page, shop_no, review.external_review_id, result.content)
                            db.add(ReviewReply(
                                review_id=review.id, reply_type="final", style_id=auto_reply_style.id,
                                content=result.content, created_at=datetime.now(timezone.utc),
                            ))
                            review.status = "answered"
                        else:
                            db.add(ReviewReply(
                                review_id=review.id, reply_type="ai_draft", style_id=auto_reply_style.id,
                                content=result.content, created_at=datetime.now(timezone.utc),
                            ))
                            review.status = "pending"
                        db.commit()
                    except Exception as e:
                        auto_reply_errors.append(f"리뷰 {review.id}(별점 {review.rating}, 기존 미답변): {e}")
```

(두 경로 모두 성공/보류 두 분기 다음에 공통으로 `db.commit()` 한 번만 호출하도록 들여쓰기 정리했다 — 기존 "제출 성공 직후 즉시 커밋"(2026-10-06 C1 수정) 원칙을 보류 분기에도 똑같이 적용한다: 보류 역시 ai_draft 저장이라는 상태 변화가 실제로 일어났으니, 이후 동기화 중 다른 이유로 롤백되면 같은 리뷰를 또 처리하려 들 수 있어 즉시 커밋이 맞다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_review_sync.py -v`
Expected: PASS 전체(기존 자동답글 테스트들도 `run_agent`가 `generate_ai_reply`를 대체했으니, 그 테스트들이 `generate_ai_reply`를 직접 monkeypatch하고 있었다면 `run_agent`를 monkeypatch하도록 같이 고칠 것 — `passed_verification=True`인 `AgentResult`를 반환하게 하면 기존 "제출 성공" 시나리오와 동일하게 동작한다)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 확인**

Run: `cd backend && python -m pytest -q`
Expected: 전부 PASS, pgvector 테스트는 로컬 Postgres 있으면 실행됨

- [ ] **Step 6: Commit**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 자동답글 경로를 run_agent+보류 메커니즘으로 교체 (게이트 조건은 유지)"
```

---

## Self-Review

**스펙 커버리지**: 2.0(프레임워크 채택) Task 1, 2.1 그래프 구조(5개 노드) Task 2-3, 재시도 2회 Task 3, 보류 정책(ai_draft 재사용) Task 5. 적용 범위 확대는 **의도적으로 범위 밖**(Global Constraints에 명시, 스펙도 같이 정정함).

**플레이스홀더 스캔**: Task 5 Step 1의 "기존 테스트 셋업을 그대로 복사해 채워 넣으라"는 지시는 Plan 1 Task 6/7과 같은 이유(실제 파일이 매우 커서 전체를 옮기면 플랜이 비대해짐)로 의도적으로 남긴 것 — "Similar to Task N"(다른 태스크 참조) 금지와는 다른 경우로 판단했다. 나머지는 전부 실제 코드.

**타입 일관성**: `run_agent`의 반환 타입(`AgentResult`, Task 3)과 Task 4/5의 소비 코드가 쓰는 필드(`content`/`passed_verification`/`retry_count`)가 일치한다. `generate_ai_reply`의 공개 시그니처(Task 4)는 Plan 1에서 이미 확정된 `reviews.py`/`reply_onboarding.py` 호출부와 그대로 맞는다(바꾸지 않았으므로).
