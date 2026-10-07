"""리뷰 답글 생성을 LangGraph 상태 그래프로 표현한다(스펙
2026-10-03-ai-agent-llmops-reply-design.md 2절). 기존 generate_ai_reply
(app/llm/generate.py)가 하던 일을 노드 5개로 쪼갠다 — retrieve_memory
(기억 조회) → generate_draft(초안 생성) → verify_draft(결정론적 검증)
→ [위반 시] fix_draft(수정) → finalize(최종 반환). LLM 재판단(자기비판)은
쓰지 않는다 — 말투가 무난하게 수렴할 위험이 있다고 판단해 기각함(스펙
2.1절), 검증은 전부 결정론적 체크로만 한다.

노드 5개와 StateGraph 조립, run_agent 진입점이 모두 이 파일에 있다.
generate.py의 generate_ai_reply 자체는 아직 건드리지 않는다 —
generate_ai_reply를 이 그래프 호출로 교체하는 것은 Task 4다.

LangSmith 트레이싱(2026-10-07, LangSmith 연동 플랜)은 run_agent이 발급하는
trace_id를 _GRAPH.invoke의 config={"run_id": ...}로 넘기는 것으로만
연결한다 — 그래프 구조/노드 자체는 건드리지 않는다."""

import difflib
import uuid
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.llm.generate import (
    CATEGORY_LABELS,
    _EMOJI_PATTERN,
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

# 재시도 상한 — 이 횟수만큼 fix_draft를 돌려도 위반이 남으면 루프를 포기하고
# passed_verification=False로 끝낸다(무한 루프 금지). 그 신호로 자동 제출
# 여부를 가르는 건 Task 5의 일이다.
_MAX_RETRIES = 2

# difflib.SequenceMatcher 비율 — 1.0이면 완전히 동일. 실측 데이터로 보정
# 전이라 보수적으로 시작한다(스펙 1.4.1의 _CONSISTENCY_DISTANCE_THRESHOLD와
# 같은 종류의 잠정값 — 운영하면서 조정 대상). 이 값으로 실제 한국어 문장을
# 재본 결과: 완전 동일 1.0, 어미 하나만 바꾼 사실상 복붙 0.95, 같은 상황을
# 다르게 쓴 의역 0.34 — 0.8이면 뒤 둘을 뚜렷이 가른다.
_COPY_PASTE_SIMILARITY_THRESHOLD = 0.8


# 이 그래프는 체크포인터를 쓰지 않는다(의도적) — state가 그래프 실행 중
# 메모리 참조로만 넘어가고 직렬화되지 않으므로 Session/ORM 객체를 그대로
# 담아도 안전하다. Plan 3(LangSmith)/Plan 4에서 체크포인터나 영속화를
# 추가한다면 Session은 피클 불가능이니 이 가정이 깨진다 — 그때 db/review/
# store를 state 밖으로 빼내거나 직렬화 가능한 형태로 바꿔야 한다.
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

    profile = db.get(StoreStyleProfile, (store.id, review.category))
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


def _apply_tone_cleanup(draft: str, tone_overridden: bool) -> str:
    """불만 리뷰(tone_overridden)의 답글에서만 이모지를 지운다 — no_issue
    (칭찬/무난) 리뷰는 페르소나 톤의 이모지를 그대로 둔다. generate_draft와
    fix_draft가 같은 규칙을 쓰도록 한 군데로 모았다."""
    return _strip_emoji(draft) if tone_overridden else draft


def verify_draft_node(state: AgentState) -> dict:
    """결정론적 체크 둘만 한다 — LLM 재판단 없음(스펙 2.1절). (a) 불만
    톤인데 이모지가 섞였는지, (b) few-shot 예시 중 하나를 사실상 그대로
    복붙했는지(SequenceMatcher 비율로 판단).

    LLM 자기비판을 안 쓰는 이유는 이 프로젝트의 북극성 목표와 정면으로
    충돌하기 때문이다 — 모델이 자기 출력을 다시 평가하면 특징적인 말투가
    무난한 "도움되는 조수" 톤으로 수렴하는 경향이 있는데, 우리가 RAG를
    도입한 목적 자체가 사장님 실제 말투 재현이다.

    (a)는 현재 그래프 배선에서는 사실상 발화하지 않는다 — generate_draft와
    fix_draft가 tone_overridden이면 이미 같은 _EMOJI_PATTERN으로 이모지를
    지우고 나오기 때문에, 그 뒤에 같은 패턴으로 검색해봐도 걸릴 수가 없다.
    그래도 남겨둔다: 이 노드가 보장하는 건 "불만 답글에 이모지가 없다"는
    사후 불변조건이고, 앞 단계가 바뀌거나(다른 생성 경로 추가) 다른 곳에서
    이 노드를 재사용할 때 그 불변조건이 조용히 깨지는 걸 막는 그물이다."""
    draft = state["draft"]
    violations: list[str] = []

    if state["tone_overridden"] and _EMOJI_PATTERN.search(draft):
        violations.append("emoji")

    copy_paste_match = _find_copy_paste_match(
        draft, state.get("examples") or [], state["display_name"],
    )
    if copy_paste_match is not None:
        violations.append("copy_paste")

    return {"violations": violations, "copy_paste_match": copy_paste_match}


def _find_copy_paste_match(
    draft: str, examples: list[GoldenExample], display_name: str,
) -> GoldenExample | None:
    """초안과 가장 많이 겹치는 예시를 돌려준다(임계값을 넘는 게 없으면
    None). "가장 많이"가 중요하다 — fix_draft의 재지시문이 "이 문장과
    비슷하다"며 예시 원문을 콕 집어 넣으므로, 여러 개가 걸렸을 때 엉뚱한
    쪽을 지목하면 지시가 어긋난다.

    비교 전에 display_name(가게/브랜드 이름)을 양쪽에서 지운다 — 이
    가게의 모든 답글이 같은 인사말로 시작해(_resolve_display_name) 그
    공통 접두부만으로도 비율이 임계값을 넘는 걸 실측 확인했다(최종 리뷰,
    2026-10-06) — 예: "치밥대장입니다. 맛있게 드셨다니..." vs
    "치밥대장입니다. 좋은 평가 감사합니다..."가 내용은 전혀 다른데도
    0.87이 나왔다. 이 값은 정말 복붙된 본문 내용을 잡으려는 것이라,
    모든 답글에 똑같이 들어가는 브랜드명은 신호가 아니라 잡음이다."""
    normalized_draft = draft.replace(display_name, "") if display_name else draft
    best: GoldenExample | None = None
    best_ratio = _COPY_PASTE_SIMILARITY_THRESHOLD
    for example in examples:
        normalized_example = (
            example.reply_text.replace(display_name, "") if display_name else example.reply_text
        )
        ratio = difflib.SequenceMatcher(None, normalized_draft, normalized_example).ratio()
        if ratio >= best_ratio:
            best, best_ratio = example, ratio
    return best


def fix_draft_node(state: AgentState) -> dict:
    """이모지만 위반이면 코드로 바로 제거한다(LLM 호출 없음 — 고치는
    쪽에도 "LLM 재판단 금지" 원칙이 그대로 적용된다). 복붙 위반이 있으면
    (이모지와 동시에 있어도) 겹친 예시를 콕 집어 "그 문장만 피해서 다시
    써라"는 좁은 지시로 딱 한 번 재생성한다 — 막연한 "다시 해봐"는
    쓰지 않는다. 재생성 결과에도 이모지 제거를 다시 적용한다.

    재생성할 때는 **겹친 예시를 few-shot 목록에서 아예 빼고** 호출한다.
    그 예시를 그대로 남겨두면 프롬프트가 "이 문장을 쓰지 마라"는 지시와
    "이 문장이 좋은 예시다"라는 데모를 동시에 들고 가는, 서로 모순되는
    신호가 된다 — 이 프로젝트는 이모지 작업에서 이미 "텍스트 지시만으로는
    few-shot 데모를 안정적으로 못 이긴다"는 걸 실측으로 확인하고, 지시를
    더 세게 쓰는 대신 예시 쪽에서 모순 신호를 지우는 방식으로 해결했다
    (CLAUDE.md "no_issue 리뷰도 RAG로 통합" 절의 _strip_emoji 처리). 같은
    이유로 여기서도 지목한 예시 하나만 빼고, 나머지 예시는 그대로 남겨
    말투 그라운딩은 유지한다. 남은 예시가 하나도 없게 되는 경우(겹친 게
    유일한 예시였을 때)도 그대로 둔다 — 그라운딩할 다른 예시가 실제로
    없는 상태이고, _build_system_prompt가 빈 목록을 "(아직 참고할 예시가
    없습니다.)"로 처리한다."""
    violations = state["violations"]
    retry_count = state["retry_count"] + 1
    tone_overridden = state["tone_overridden"]
    match = state.get("copy_paste_match")

    if "copy_paste" not in violations or match is None:
        # 이모지만 위반이거나(결정론적으로 고칠 수 있다), 복붙 위반인데
        # 지목할 예시 객체가 없는 비정상 상태 — 어느 쪽이든 재생성 지시를
        # 좁게 만들 수 없으니 LLM을 부르지 않는다.
        return {
            "draft": _apply_tone_cleanup(state["draft"], tone_overridden),
            "retry_count": retry_count,
        }

    extra_instruction = (
        f'방금 만든 답글이 다음 예시와 너무 비슷합니다: "{match.reply_text}". '
        "이 문장을 그대로 쓰지 말고, 같은 상황이지만 표현을 완전히 새로 바꿔서 다시 작성하세요."
    )
    remaining_examples = [ex for ex in (state.get("examples") or []) if ex is not match]
    patch = generate_draft_node(
        {**state, "examples": remaining_examples}, extra_instruction=extra_instruction,
    )
    return {
        "draft": _apply_tone_cleanup(patch["draft"], tone_overridden),
        "retry_count": retry_count,
    }


def route_after_verify(state: AgentState) -> str:
    """verify_draft 뒤 분기 — 깨끗하면 끝내고, 위반이 있으면 재시도 상한이
    남았을 때만 fix_draft로 보낸다. 상한에 닿으면 예외를 던지지 않고
    그냥 끝낸다(위반이 남은 채로 finalize → passed_verification=False)."""
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


# 모듈 import 시 한 번만 컴파일한다 — 그래프 구조는 요청마다 달라지지 않고,
# 상태(db/review/store/style)는 invoke 인자로만 들어간다.
_GRAPH = _build_graph()


@dataclass
class AgentResult:
    content: str
    passed_verification: bool
    retry_count: int
    trace_id: str


def run_agent(db: Session, review: Review, store: Store, style: ReplyStyle) -> AgentResult:
    """그래프 진입점. passed_verification=False면 결정론적 검증을 끝까지
    통과하지 못한 초안이라는 뜻이다 — 그래도 content는 돌려준다(사장님이
    직접 고쳐 쓸 수 있도록). 이 신호로 자동 제출 여부를 가르는 건 Task 5.

    trace_id는 이 호출 하나를 가리키는 LangSmith run id다 — 직접 uuid4로
    만들어서 config["run_id"]로 명시적으로 넘긴다(LangSmith가 자동으로
    매기게 두면 호출부가 나중에 이 trace를 다시 찾아갈 방법이 없다).
    LANGSMITH_TRACING_V2가 설정 안 돼 있으면 이 config는 그냥 무시되고
    아무 네트워크 호출도 없다 — trace_id 자체는 항상 발급되고(테스트/로컬
    환경에서도) 호출부가 저장해두는 값이라, 나중에 LANGSMITH_TRACING_V2를
    켜면 그때부터의 trace만 실제로 LangSmith에 남는다. category/store_id를
    metadata로 같이 보내는 이유는 측정 대시보드(Task 6)가 카테고리별로
    집계해야 하기 때문(스펙 4.2절)."""
    trace_id = uuid.uuid4()
    final_state = _GRAPH.invoke(
        {"db": db, "review": review, "store": store, "style": style},
        config={
            "run_id": trace_id,
            "metadata": {"category": review.category, "store_id": store.id},
            "tags": ["agent_graph"],
        },
    )
    return AgentResult(
        content=final_state["final_content"],
        passed_verification=final_state["passed_verification"],
        retry_count=final_state["retry_count"],
        trace_id=str(trace_id),
    )
