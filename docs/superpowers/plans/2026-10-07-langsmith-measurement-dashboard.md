# LangSmith 연동 + 측정 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 답글 생성 LangGraph 루프(`backend/app/llm/agent_graph.py`)에 LangSmith 트레이싱을 붙이고, AI 초안과 사장님 최종본의 코사인 유사도를 그 trace에 feedback으로 기록하면서 자체 DB에도 저장해, 카테고리별로 "AI가 사장님 말투에 얼마나 수렴하고 있나"를 대시보드 숫자로 보여준다.

**Architecture:** `run_agent`(이미 존재, Plan 2)이 그래프를 호출할 때 LangChain `RunnableConfig`로 직접 만든 `run_id`(trace_id)와 `metadata`(category/store_id)를 넘긴다. 이 trace_id를 `ReviewReply`(ai_draft)/`OnboardingScenario`에 저장해두면, 나중에 사장님이 그 초안을 고쳐서 최종 저장할 때(`save_final_reply`/`answer_scenario`) 초안-최종본 쌍의 Voyage 임베딩 코사인 유사도를 계산해 (1) 새 테이블 `draft_feedback_scores`에 적재하고 (2) 같은 trace에 LangSmith feedback으로도 기록한다. 대시보드는 `draft_feedback_scores`를 카테고리별로 집계해 보여준다(LangSmith는 관측/디버깅용 사이드 채널이고, 대시보드는 자체 DB를 읽는다 — LangSmith REST API를 페이지네이션하며 집계하지 않는다).

**Tech Stack:** `langsmith`(Client.create_feedback, `RunnableConfig`의 `run_id`/`metadata`/`tags`), 기존 Voyage AI 임베딩(`app/llm/embedding.py`), Alembic, SQLAlchemy, FastAPI `BackgroundTasks`, Next.js(기존 대시보드 페이지 확장).

## Global Constraints

- **측정 대상은 경로 A(`save_final_reply`)와 경로 B(`answer_scenario`)뿐이다** — 경로 C(배민 직접 답글)와 검증 통과 후 사람 개입 없이 자동 제출된 답글은 비교할 초안/사람 개입이 없어 측정 대상이 아니다(스펙 4.2절 "적용 범위"). 두 경로 모두 비교에 쓸 draft text가 이미 trace_id와 함께 저장돼 있을 때만 측정한다.
- **LLM-judge 금지, 순수 벡터 연산만 쓴다** — AI 초안과 최종본의 유사도는 Voyage 임베딩의 코사인 유사도로만 계산한다(스펙 4.2절, "판단 편향 위험"으로 LLM-judge 명시적으로 기각).
- **category를 반드시 trace 메타데이터로 함께 기록한다** — 대시보드가 카테고리별로 따로 추세를 보여줘야 하기 때문(스펙 4.2절).
- **Voyage/LangSmith 중 어느 하나라도 실패해도 메인 기능(답글 저장)을 막지 않는다** — 이 프로젝트의 다른 LLM 폴백들(임베딩 실패 시 최신순 폴백, 분류 실패 시 기본값 폴백)과 동일한 원칙. 측정은 항상 "있으면 좋은 부가 기능"이고, 실패하면 조용히 스킵한다.
- **`generate_ai_reply(db, review, store, style) -> str`의 기존 공개 시그니처는 그대로 유지한다** — Plan 2에서 못박은 제약이고 지금도 `reviews.py`/`onboarding.py`가 이 시그니처를 그대로 쓴다. trace_id가 필요한 호출부는 새 함수 `generate_ai_reply_with_trace`로 옮겨가고, `generate_ai_reply`는 그 위의 1줄 래퍼로 남는다.
- **`AgentState`에 체크포인터를 추가하지 않는다** — Plan 2의 결정(`agent_graph.py`의 `AgentState` 바로 위 주석 참고, Session/ORM 객체를 state에 직접 들고 다님)을 그대로 유지한다. 이 플랜은 `_GRAPH.invoke(state, config=...)`의 `config` 인자만 추가로 넘긴다 — LangGraph의 상태 저장 방식 자체는 바꾸지 않는다.
- **새 의존성은 상한만 두고 핀한다** — Plan 2가 `langchain-anthropic`/`langgraph`에 적용한 것과 같은 이유(내부 동작 의존, 메이저 업그레이드가 백엔드 전체 import를 깨뜨릴 위험)로 `langsmith`도 명시적으로 상한을 둔다: `langsmith>=0.14,<1`.
- **`similarity_score`는 -1~1 범위를 그대로 허용한다(클램핑 없음)** — 코사인 유사도가 이론상 음수가 나올 수 있는데, 실제로 음수가 나온다면 그 자체가 유의미한 신호(측정 이상)라 0으로 뭉개지 않는다. 이 프로젝트가 다른 곳(정산 "기타" 금액)에서도 음수를 그대로 드러내는 것과 같은 선택.

---

### Task 1: LangGraph 트레이싱 — `run_agent`이 trace_id를 발급하고 그래프 호출에 실어 보낸다

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`
- Modify: `backend/app/llm/agent_graph.py:1-20`(모듈 docstring), `:270-286`(`AgentResult`/`run_agent`)
- Test: `backend/tests/test_llm_agent_graph.py`

**Interfaces:**
- Consumes: 없음(이 태스크가 LangSmith 연동의 시작점).
- Produces: `AgentResult`에 새 필드 `trace_id: str`(UUID 문자열). `run_agent(db, review, store, style) -> AgentResult`의 반환값에 `trace_id`가 항상 채워져 있다는 보장 — 이후 모든 태스크가 이 필드를 읽는다.

- [ ] **Step 1: `requirements.txt`에 `langsmith` 명시 핀 추가**

현재 `langsmith`는 `langchain-anthropic`/`langgraph`의 transitive dependency로만 설치돼 있다(설치 버전 `0.14.4`). 이번 태스크부터 `Client.create_feedback`을 직접 호출하므로 명시적으로 추가한다.

`backend/requirements.txt` 15번째 줄(`langgraph>=1,<2`) 바로 뒤에 추가:

```
langsmith>=0.14,<1
```

- [ ] **Step 2: `.env.example`에 LangSmith 환경변수 추가**

`backend/.env.example` 맨 끝(`TOSS_SECRET_KEY=` 다음)에 추가:

```
# LangSmith 트레이싱 + AI초안-사장님최종본 유사도 피드백 기록(답글 생성
# 에이전트 루프 관측). smith.langchain.com에서 발급. 비워두면 트레이싱도
# 피드백 기록도 조용히 스킵되고(에러 없음) 답글 생성 자체는 그대로
# 동작한다 — 이 프로젝트의 다른 LLM 폴백과 같은 원칙.
LANGSMITH_TRACING_V2=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=review-docter
```

- [ ] **Step 3: 실패하는 테스트 작성**

`backend/tests/test_llm_agent_graph.py` 맨 아래(`test_run_agent_propagates_node_exceptions_unwrapped` 다음)에 추가:

```python
import uuid


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
```

이 파일에 이미 `_make_review` 헬퍼가 있는지 확인해라(Plan 2의
`test_run_agent_propagates_node_exceptions_unwrapped`/
`test_run_agent_does_not_hit_langgraph_recursion_limit` 테스트가 이미
`_make_review(db_session, store, platforms)`를 쓰고 있다 — 같은 헬퍼를
재사용하면 된다, 새로 만들지 마라).

- [ ] **Step 4: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_llm_agent_graph.py -k "trace_id" -v`
Expected: FAIL — `AttributeError: 'AgentResult' object has no attribute 'trace_id'`

- [ ] **Step 5: `agent_graph.py`에 trace_id 발급 구현**

파일 맨 위 import 블록(`import difflib` 다음)에 `import uuid` 추가.

`AgentResult` 데이터클래스(`:270-273` 근처)를 다음으로 교체:

```python
@dataclass
class AgentResult:
    content: str
    passed_verification: bool
    retry_count: int
    trace_id: str
```

`run_agent` 함수(`:276-286` 근처)를 다음으로 교체:

```python
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
```

모듈 docstring 맨 끝(`generate_ai_reply를 이 그래프 호출로 교체하는 것은
Task 4다."""` 부분)에 한 문단 추가:

```
LangSmith 트레이싱(2026-10-07, LangSmith 연동 플랜)은 run_agent이 발급하는
trace_id를 _GRAPH.invoke의 config={"run_id": ...}로 넘기는 것으로만
연결한다 — 그래프 구조/노드 자체는 건드리지 않는다.
```

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_llm_agent_graph.py -v`
Expected: PASS(새 테스트 2개 포함, 기존 테스트도 전부 그대로 PASS — 특히
`test_run_agent_propagates_node_exceptions_unwrapped`가 `config` 인자
추가로 깨지지 않는지 확인).

- [ ] **Step 7: 커밋**

```bash
git add backend/requirements.txt backend/.env.example backend/app/llm/agent_graph.py backend/tests/test_llm_agent_graph.py
git commit -m "feat: run_agent이 LangSmith trace_id를 발급해 그래프 호출에 싣는다"
```

---

### Task 2: DB 마이그레이션 — `trace_id` 컬럼 2개 + `draft_feedback_scores` 테이블

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0003_draft_feedback_scores.py`
- Modify: `schema.sql`
- Test: `backend/tests/test_models_draft_feedback_score.py`(신규)

**Interfaces:**
- Consumes: 없음(독립적인 스키마 변경).
- Produces: `ReviewReply.trace_id: str | None`, `OnboardingScenario.trace_id: str | None` 컬럼. 신규 모델
  `DraftFeedbackScore(id, store_id, category, similarity_score, trace_id, source_review_id, source_scenario_id, created_at)`.
  이후 태스크 전부가 이 컬럼/테이블에 쓰거나 읽는다.

- [ ] **Step 1: `models.py`에 `trace_id` 컬럼 2개 추가**

`backend/app/models.py`의 `ReviewReply` 클래스(`:252-262`)를 다음으로 교체:

```python
class ReviewReply(Base):
    __tablename__ = "review_replies"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    review_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    reply_type: Mapped[str] = mapped_column(String(10))
    style_id: Mapped[int | None] = mapped_column(ForeignKey("reply_styles.id"))
    content: Mapped[str] = mapped_column(Text)
    # reply_type="ai_draft" 행에만 채워진다 — 이 초안을 만든 run_agent
    # 호출의 LangSmith trace id(app/llm/agent_graph.py의 run_agent).
    # 사장님이 나중에 이 초안을 고쳐서 save_final_reply로 저장하면,
    # 그 쌍의 유사도를 이 trace에 feedback으로 기록한다(스펙 4.2절,
    # app/llm/feedback.py). "final"/"secondary" 행에는 없다(NULL).
    trace_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime]

    review: Mapped[Review] = relationship(back_populates="replies")
```

`OnboardingScenario` 클래스(`:433-446`)를 다음으로 교체:

```python
class OnboardingScenario(Base):
    __tablename__ = "onboarding_scenarios"
    __table_args__ = (UniqueConstraint("store_id", "category"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    virtual_review_text: Mapped[str] = mapped_column(Text)
    draft_text: Mapped[str] = mapped_column(Text)
    # draft_text를 만든 run_agent 호출의 LangSmith trace id — ReviewReply.trace_id와
    # 같은 용도(경로 B 측정, app/llm/feedback.py).
    trace_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    shown_on: Mapped[date | None]
    created_at: Mapped[datetime]

    store: Mapped[Store] = relationship()
```

`OnboardingScenario` 클래스 바로 뒤(파일 맨 끝)에 새 모델 추가:

```python
class DraftFeedbackScore(Base):
    """AI 초안과 사장님 최종본의 코사인 유사도 1건(스펙 4.2절 "순환 측정
    장치") — 경로 A(save_final_reply)/경로 B(answer_scenario) 둘 다 이
    테이블에 쓴다. 집계(카테고리별 평균)는 대시보드가 조회 시점에 한다
    (daily_settlements와 같은 정규화 원칙 — 요약을 별도로 캐싱하지
    않는다)."""
    __tablename__ = "draft_feedback_scores"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    # -1~1 범위 그대로 허용(클램핑 없음) — 코사인 유사도가 음수로 나오면
    # 그 자체가 측정 이상 신호라 0으로 뭉개지 않는다.
    similarity_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    # 이 측정이 어느 LangSmith trace의 feedback인지 — DB에도 같은 값을
    # 들고 있어야 "이 점수가 어느 생성 시도였는지" DB만 보고도 추적된다.
    trace_id: Mapped[str] = mapped_column(String(36))
    source_review_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    source_scenario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("onboarding_scenarios.id"))
    created_at: Mapped[datetime]
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_models_draft_feedback_score.py` 새로 작성:

```python
from datetime import datetime, timezone

from app.models import DraftFeedbackScore, OnboardingScenario, ReviewReply


def test_review_reply_has_nullable_trace_id_column(db_session, seeded_user, platforms):
    from app.models import Review

    store = seeded_user["store"]
    platform = platforms["baemin"]
    review = Review(
        store_id=store.id, platform_id=platform.id, menu_summary="치킨", rating=5,
        content="맛있어요", customer_nickname="손님", customer_order_count=1,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()

    reply = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=None,
        content="감사합니다", trace_id="11111111-1111-1111-1111-111111111111",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(reply)
    db_session.commit()

    fetched = db_session.get(ReviewReply, reply.id)
    assert fetched.trace_id == "11111111-1111-1111-1111-111111111111"


def test_onboarding_scenario_has_nullable_trace_id_column(db_session, seeded_user):
    store = seeded_user["store"]
    scenario = OnboardingScenario(
        store_id=store.id, category="delivery", virtual_review_text="배달이 늦었어요",
        draft_text="불편드려 죄송합니다", trace_id="22222222-2222-2222-2222-222222222222",
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    fetched = db_session.get(OnboardingScenario, scenario.id)
    assert fetched.trace_id == "22222222-2222-2222-2222-222222222222"


def test_draft_feedback_score_round_trips(db_session, seeded_user):
    store = seeded_user["store"]
    score = DraftFeedbackScore(
        store_id=store.id, category="food_quality", similarity_score=0.8421,
        trace_id="33333333-3333-3333-3333-333333333333",
        source_review_id=None, source_scenario_id=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(score)
    db_session.commit()

    fetched = db_session.get(DraftFeedbackScore, score.id)
    assert fetched.category == "food_quality"
    assert float(fetched.similarity_score) == 0.8421
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_models_draft_feedback_score.py -v`
Expected: FAIL — `TypeError: 'trace_id' is an invalid keyword argument for ReviewReply`(컬럼이 아직 없음)

- [ ] **Step 4: Alembic 마이그레이션 작성**

`backend/alembic/versions/0003_draft_feedback_scores.py` 새로 작성:

```python
"""draft feedback scores

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07

LangSmith 연동 + 측정 대시보드 플랜(docs/superpowers/plans/
2026-10-07-langsmith-measurement-dashboard.md)의 스키마 변경. 내용:
  - review_replies.trace_id, onboarding_scenarios.trace_id — 이 초안을
    만든 run_agent 호출의 LangSmith run id(nullable, 기존 행은 전부
    NULL로 남는다 — 소급 측정 대상 아님).
  - draft_feedback_scores 신규 테이블 — AI 초안 vs 사장님 최종본 코사인
    유사도 1건당 1행(스펙 4.2절 "순환 측정 장치").
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE review_replies ADD COLUMN trace_id VARCHAR(36)")
    op.execute("ALTER TABLE onboarding_scenarios ADD COLUMN trace_id VARCHAR(36)")

    op.execute("""
        CREATE TABLE draft_feedback_scores (
            id                 BIGSERIAL PRIMARY KEY,
            store_id           BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            category           VARCHAR(24)  NOT NULL,
            similarity_score   NUMERIC(5,4) NOT NULL CHECK (similarity_score BETWEEN -1 AND 1),
            trace_id           VARCHAR(36)  NOT NULL,
            source_review_id   BIGINT       REFERENCES reviews(id) ON DELETE SET NULL,
            source_scenario_id BIGINT       REFERENCES onboarding_scenarios(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_draft_feedback_scores_lookup ON draft_feedback_scores(store_id, category, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE draft_feedback_scores")
    op.execute("ALTER TABLE onboarding_scenarios DROP COLUMN trace_id")
    op.execute("ALTER TABLE review_replies DROP COLUMN trace_id")
```

- [ ] **Step 5: `schema.sql` 손으로 동기화**

`schema.sql`은 정본이 아니라 사람이 읽는 스냅샷이다(CLAUDE.md "스키마 변경
절차" 절) — 위 마이그레이션의 DDL을 그대로 반영해 손으로 고친다:

1. `review_replies` 테이블 정의에 `trace_id VARCHAR(36),` 컬럼 한 줄 추가
   (`style_id` 다음, `content` 전후 중 실제 물리 순서에 맞는 자리 — ALTER로
   붙었으니 테이블 끝 쪽, `created_at` 바로 앞에 추가).
2. `onboarding_scenarios` 테이블 정의에 같은 방식으로 `trace_id VARCHAR(36),`
   추가(`created_at` 바로 앞).
3. `draft_feedback_scores` 테이블을 파일에서 가장 최근에 추가된 테이블
   (`procedural_rules`/`brand_ceo_notices`) 뒤에 새로 추가하고, 파일 맨 위
   `DROP TABLE IF EXISTS` 목록과 테이블 수 주석에도 반영한다(현재 28개 →
   29개). CHECK/인덱스는 위 마이그레이션 SQL과 완전히 동일하게 적는다.

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_models_draft_feedback_score.py -v`
Expected: PASS

- [ ] **Step 7: 전체 테스트 스위트 돌려서 회귀 없는지 확인**

Run: `cd backend && pytest`
Expected: 기존 테스트 전부 PASS(컬럼 추가는 nullable이라 기존 데이터/코드에
영향 없음).

- [ ] **Step 8: 커밋**

```bash
git add backend/app/models.py backend/alembic/versions/0003_draft_feedback_scores.py schema.sql backend/tests/test_models_draft_feedback_score.py
git commit -m "feat: review_replies/onboarding_scenarios trace_id 컬럼 + draft_feedback_scores 테이블 추가"
```

---

### Task 3: 코사인 유사도 계산 + `record_draft_feedback_background`(DB 기록 + LangSmith feedback)

**Files:**
- Modify: `backend/app/llm/embedding.py`
- Create: `backend/app/llm/feedback.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_llm_feedback.py`(신규)

**Interfaces:**
- Consumes: Task 2의 `DraftFeedbackScore` 모델.
- Produces: `cosine_similarity(a: list[float], b: list[float]) -> float`(`app/llm/embedding.py`).
  `record_draft_feedback_background(*, trace_id: str, draft_text: str, final_text: str, store_id: int, category: str, source_review_id: int | None = None, source_scenario_id: int | None = None) -> None`(`app/llm/feedback.py`) — Task 5가
  이 함수를 `BackgroundTasks.add_task`로 호출한다.

- [ ] **Step 1: `conftest.py`에 LangSmith 키 autouse fixture 추가**

`backend/tests/conftest.py`의 `_no_voyage_key` autouse fixture 바로 뒤에
같은 패턴으로 추가(기존 두 fixture의 정확한 코드를 먼저 읽고 그 스타일을
그대로 따라라 — 이름만 바꾼 완전한 복붙이면 된다):

```python
@pytest.fixture(autouse=True)
def _no_langsmith_key(monkeypatch):
    for key in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(key, raising=False)
```

- [ ] **Step 2: 실패하는 테스트 작성 — `cosine_similarity`**

`backend/tests/test_llm_feedback.py` 새로 작성:

```python
import pytest

from app.llm.embedding import cosine_similarity
from app.llm.feedback import record_draft_feedback_background
from app.models import DraftFeedbackScore


def test_cosine_similarity_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero_without_raising():
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_llm_feedback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.feedback'`

- [ ] **Step 4: `embedding.py`에 `cosine_similarity` 추가**

`backend/app/llm/embedding.py` 맨 끝(`embed_query` 함수 다음)에 추가:

```python
def cosine_similarity(a: list[float], b: list[float]) -> float:
    """두 임베딩 벡터의 코사인 유사도(-1~1). golden_examples 검색이 쓰는
    pgvector의 <-> 연산자(DB 레벨, 거리)와 달리, 이건 두 벡터를 이미
    메모리에 들고 있을 때 쓰는 순수 Python 계산이다(app/llm/feedback.py가
    AI 초안과 사장님 최종본의 유사도를 잴 때 쓴다, 스펙 4.2절).
    둘 중 하나가 영벡터면(이론상으로만 가능, 실제 텍스트 임베딩에서는
    안 나옴) 0으로 돌려준다 — ZeroDivisionError를 내는 대신."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

- [ ] **Step 5: 테스트 실행해서 `cosine_similarity` 통과 확인**

Run: `cd backend && pytest tests/test_llm_feedback.py -v`
Expected: 여전히 FAIL(이제는 `cosine_similarity` 4개는 PASS, `app.llm.feedback`
import가 없어서 수집 단계에서 에러 — 다음 스텝에서 만든다).

- [ ] **Step 6: `record_draft_feedback_background`에 대한 나머지 테스트 추가**

같은 파일(`test_llm_feedback.py`)에 이어서 추가:

```python
def test_record_draft_feedback_background_writes_score_row(db_session, seeded_user, monkeypatch):
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]
    monkeypatch.setattr(feedback_mod, "embed_documents", lambda texts: [[1.0, 0.0], [1.0, 0.0]])
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)
    created_feedback = []

    class _FakeClient:
        def create_feedback(self, **kwargs):
            created_feedback.append(kwargs)

    monkeypatch.setattr(feedback_mod, "_LangSmithClient", _FakeClient)

    record_draft_feedback_background(
        trace_id="44444444-4444-4444-4444-444444444444",
        draft_text="초안입니다", final_text="최종본입니다",
        store_id=store.id, category="food_quality",
        source_review_id=None, source_scenario_id=None,
    )

    row = db_session.query(DraftFeedbackScore).one()
    assert row.category == "food_quality"
    assert float(row.similarity_score) == pytest.approx(1.0)
    assert created_feedback == [{
        "run_id": "44444444-4444-4444-4444-444444444444",
        "key": "draft_final_similarity", "score": pytest.approx(1.0),
    }]


def test_record_draft_feedback_background_skips_silently_when_embedding_fails(db_session, seeded_user, monkeypatch):
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]

    def _boom(texts):
        raise RuntimeError("Voyage 호출 실패")

    monkeypatch.setattr(feedback_mod, "embed_documents", _boom)
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)

    record_draft_feedback_background(
        trace_id="55555555-5555-5555-5555-555555555555",
        draft_text="초안", final_text="최종본",
        store_id=store.id, category="delivery",
    )

    assert db_session.query(DraftFeedbackScore).count() == 0


def test_record_draft_feedback_background_keeps_db_row_when_langsmith_fails(db_session, seeded_user, monkeypatch):
    """DB 기록은 LangSmith 기록과 독립적이어야 한다 — LangSmith가 실패해도
    이미 계산된 유사도 점수는 DB에 남아야 한다(대시보드가 이 DB만 읽는다)."""
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]
    monkeypatch.setattr(feedback_mod, "embed_documents", lambda texts: [[1.0, 0.0], [0.0, 1.0]])
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)

    class _FailingClient:
        def create_feedback(self, **kwargs):
            raise RuntimeError("LangSmith API 실패")

    monkeypatch.setattr(feedback_mod, "_LangSmithClient", _FailingClient)

    record_draft_feedback_background(
        trace_id="66666666-6666-6666-6666-666666666666",
        draft_text="초안", final_text="완전히 다른 최종본",
        store_id=store.id, category="service",
    )

    row = db_session.query(DraftFeedbackScore).one()
    assert float(row.similarity_score) == pytest.approx(0.0)
```

- [ ] **Step 7: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_llm_feedback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.feedback'`

- [ ] **Step 8: `app/llm/feedback.py` 구현**

`backend/app/llm/feedback.py` 새로 작성:

```python
"""AI 초안과 사장님 최종본의 유사도를 기록한다(스펙 4.2절 "순환 측정
장치") — "보류→교정" 순환이 실제로 작동하는지(사장님 교정이 AI 초안에서
얼마나 벗어나는지) 숫자로 확인하는 장치다. 경로 A(reviews.py의
save_final_reply)와 경로 B(reply_onboarding.py의 answer_scenario) 둘 다
이 함수를 쓴다.

FastAPI BackgroundTasks가 호출하는 얇은 래퍼라 자체 SessionLocal을 연다
(app/llm/rag.py의 compute_golden_example_embedding_background와 동일한
패턴 — 요청 스코프 세션은 이미 닫혀 있을 수 있다).

Voyage 임베딩 계산과 LangSmith feedback 기록은 서로 독립적인 두 단계로
나눈다 — DB 기록(핵심, 대시보드가 읽는 자리)이 LangSmith API 상태에
발목잡히면 안 되기 때문이다. Voyage 호출 자체가 실패하면(키 미설정 등)
측정 전체를 스킵한다(점수가 없는데 억지로 0을 넣는 것보다 안전한 쪽).
LangSmith 쪽은 DB 기록이 끝난 뒤 별도로 시도하고 실패해도 이미 쓴 DB
행에는 영향 없다."""

from app.db import SessionLocal
from app.llm.embedding import cosine_similarity, embed_documents
from app.models import DraftFeedbackScore

try:
    from langsmith import Client as _LangSmithClient
except ImportError:  # pragma: no cover — langsmith는 requirements.txt에 있어 항상 설치돼 있다
    _LangSmithClient = None


def record_draft_feedback_background(
    *, trace_id: str, draft_text: str, final_text: str, store_id: int, category: str,
    source_review_id: int | None = None, source_scenario_id: int | None = None,
) -> None:
    try:
        draft_vec, final_vec = embed_documents([draft_text, final_text])
        score = cosine_similarity(draft_vec, final_vec)
    except Exception:
        return  # Voyage 호출 실패(키 미설정 등) — 측정 자체를 스킵한다

    db = SessionLocal()
    try:
        db.add(DraftFeedbackScore(
            store_id=store_id, category=category, similarity_score=score,
            trace_id=trace_id, source_review_id=source_review_id,
            source_scenario_id=source_scenario_id,
        ))
        db.commit()
    finally:
        db.close()

    try:
        _LangSmithClient().create_feedback(run_id=trace_id, key="draft_final_similarity", score=score)
    except Exception:
        pass  # LangSmith 기록 실패해도 위 DB 기록은 이미 끝났다
```

- [ ] **Step 9: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_llm_feedback.py -v`
Expected: PASS(전체 7개 테스트).

- [ ] **Step 10: 커밋**

```bash
git add backend/app/llm/embedding.py backend/app/llm/feedback.py backend/tests/conftest.py backend/tests/test_llm_feedback.py
git commit -m "feat: AI초안-최종본 코사인 유사도 계산 + DB/LangSmith feedback 기록 함수 추가"
```

---

### Task 4: 초안 생성 호출부가 trace_id를 받아서 저장한다 — `generate_ai_reply_with_trace`, 수동 생성 버튼, 훈련카드

**Files:**
- Modify: `backend/app/llm/generate.py`(파일 맨 끝 `generate_ai_reply` 함수)
- Modify: `backend/app/routers/reviews.py:106-145`(`generate_reply` 엔드포인트)
- Modify: `backend/app/llm/onboarding.py:84-100`(`get_or_create_scenario`)
- Test: `backend/tests/test_llm_generate.py`, `backend/tests/test_reviews.py`, `backend/tests/test_llm_onboarding.py`

**Interfaces:**
- Consumes: Task 1의 `AgentResult.trace_id`, Task 2의 `ReviewReply.trace_id`/`OnboardingScenario.trace_id` 컬럼.
- Produces: `generate_ai_reply_with_trace(db, review, store, style) -> tuple[str, str]`(`app/llm/generate.py`) — Task 5가
  이 함수의 존재를 전제로 하지는 않는다(Task 5는 이미 DB에 저장된 `trace_id`만
  읽는다), 하지만 이 태스크가 끝나야 신규로 생성되는 ai_draft/시나리오에
  실제로 trace_id가 채워진다.

- [ ] **Step 1: 실패하는 테스트 작성 — `generate_ai_reply_with_trace`**

`backend/tests/test_llm_generate.py`에서 기존에 `generate_ai_reply`를
monkeypatch 없이 직접 테스트하는 부분을 찾아 그 근처에 추가(이 파일이
`run_agent`을 어떻게 monkeypatch하는지 먼저 읽고 같은 패턴을 따라라):

```python
def test_generate_ai_reply_with_trace_returns_content_and_trace_id(monkeypatch):
    import app.llm.generate as generate_mod

    class _FakeResult:
        content = "생성된 답글"
        trace_id = "77777777-7777-7777-7777-777777777777"

    monkeypatch.setattr("app.llm.agent_graph.run_agent", lambda *a, **kw: _FakeResult())

    content, trace_id = generate_mod.generate_ai_reply_with_trace(None, None, None, None)

    assert content == "생성된 답글"
    assert trace_id == "77777777-7777-7777-7777-777777777777"


def test_generate_ai_reply_still_returns_only_content(monkeypatch):
    """기존 공개 시그니처(str 반환)는 바뀌면 안 된다 — reviews.py 밖의
    호출부가 더 있을 수 있어 Plan 2에서 못박은 제약(Global Constraints
    참고)."""
    import app.llm.generate as generate_mod

    class _FakeResult:
        content = "생성된 답글"
        trace_id = "88888888-8888-8888-8888-888888888888"

    monkeypatch.setattr("app.llm.agent_graph.run_agent", lambda *a, **kw: _FakeResult())

    result = generate_mod.generate_ai_reply(None, None, None, None)

    assert result == "생성된 답글"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_llm_generate.py -k "with_trace or still_returns" -v`
Expected: FAIL — `AttributeError: module 'app.llm.generate' has no attribute 'generate_ai_reply_with_trace'`

- [ ] **Step 3: `generate.py` 수정**

`backend/app/llm/generate.py` 맨 끝의 `generate_ai_reply` 함수를 다음으로
교체:

```python
def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    """app/llm/agent_graph.py의 LangGraph 루프를 돌리고 최종 텍스트만
    반환한다 — 기존 호출부 중 trace_id가 필요 없는 곳(지금은 없지만,
    새 호출부를 추가할 때 trace 추적이 필요 없다면 이 함수를 그대로 써도
    된다)은 이 공개 시그니처를 그대로 쓴다(Plan 2에서 못박은 제약). 검증
    결과(통과/보류)가 필요한 review_sync.py의 자동답글 경로는 이 함수가
    아니라 run_agent을 직접 쓴다(Plan 2 Task 5). trace_id가 필요한 호출부
    (reviews.py의 수동 생성 버튼, onboarding.py의 훈련카드 초안 — 둘 다
    나중에 사장님이 고쳐 쓴 최종본과의 유사도를 재야 한다, 스펙 4.2절)는
    generate_ai_reply_with_trace를 쓴다."""
    return generate_ai_reply_with_trace(db, review, store, style)[0]


def generate_ai_reply_with_trace(db: Session, review: Review, store: Store, style: ReplyStyle) -> tuple[str, str]:
    """generate_ai_reply와 동일하지만 LangSmith trace_id도 함께 반환한다.
    호출부가 이 trace_id를 ReviewReply.trace_id/OnboardingScenario.trace_id에
    저장해두면, app/llm/feedback.py가 나중에 그 trace에 유사도 feedback을
    붙일 수 있다."""
    from app.llm.agent_graph import run_agent

    result = run_agent(db, review, store, style)
    return result.content, result.trace_id
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_llm_generate.py -v`
Expected: PASS(새 테스트 2개 포함, 기존 테스트 전부 그대로 PASS).

- [ ] **Step 5: `reviews.py`의 수동 생성 버튼이 trace_id를 저장하도록 실패하는 테스트 작성**

`backend/tests/test_reviews.py`에서 `/reviews/{id}/generate-reply`를
테스트하는 기존 테스트 근처에(이 파일이 `generate_ai_reply`를 어떻게
monkeypatch하는지 먼저 읽어라 — conftest의 `_mock_generate_ai_reply`
autouse fixture가 지금 `app.routers.reviews.generate_ai_reply`를
monkeypatch하고 있다. 이 태스크부터 `reviews.py`는
`generate_ai_reply_with_trace`를 쓰므로, **이 autouse fixture도 함께
고쳐야 한다** — Step 7에서 처리) 추가:

이 파일 맨 위에 이미 있는 `make_review(db_session, store, platforms,
rating, content="테스트 리뷰")` 헬퍼를 재사용해라(직접 `Review(...)`를
새로 만들지 마라):

```python
def test_generate_reply_stores_trace_id_on_draft(client, auth_headers, db_session, seeded_user, platforms, reply_styles, monkeypatch):
    import app.routers.reviews as reviews_mod
    from app.models import ReviewReply

    store = seeded_user["store"]
    review = make_review(db_session, store, platforms, rating=5, content="맛있어요")

    monkeypatch.setattr(
        reviews_mod, "generate_ai_reply_with_trace",
        lambda db, review, store, style: ("생성된 답글", "99999999-9999-9999-9999-999999999999"),
    )

    resp = client.post(
        f"/reviews/{review.id}/generate-reply",
        json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert resp.status_code == 200
    draft = db_session.query(ReviewReply).filter_by(review_id=review.id, reply_type="ai_draft").one()
    assert draft.trace_id == "99999999-9999-9999-9999-999999999999"
```

- [ ] **Step 6: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_reviews.py -k "stores_trace_id" -v`
Expected: FAIL — `AttributeError: module 'app.routers.reviews' has no attribute 'generate_ai_reply_with_trace'`

- [ ] **Step 7: `reviews.py`/`conftest.py` 수정**

`backend/app/routers/reviews.py:17`의

```python
from app.llm.generate import generate_ai_reply
```

을

```python
from app.llm.generate import generate_ai_reply_with_trace
```

로 바꾸고, `generate_reply` 함수(`:128-144`)를 다음으로 교체:

```python
    try:
        content, trace_id = generate_ai_reply_with_trace(db, review, review.store, style)
    except Exception:
        raise HTTPException(
            503,
            detail={"message": "AI 답글 생성에 실패했어요. 잠시 후 다시 시도해주세요.", "error_code": "ai_generation_failed"},
        )
    tone_overridden = review.is_sensitive or review.sentiment_conflict

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, trace_id=trace_id, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id, "tone_overridden": tone_overridden}
```

`backend/tests/conftest.py`의 `_mock_generate_ai_reply` autouse fixture
(`:39-51`)를 다음으로 교체 — 이 fixture는 지금 `reviews_mod.generate_ai_reply`를
monkeypatch하는데, 위 Step 7에서 `reviews.py`가 더 이상 그 이름을 import하지
않게 바꿨으므로(이제 `generate_ai_reply_with_trace`만 import한다) 이
fixture를 안 고치면 `monkeypatch.setattr`이 `AttributeError`를 던져서
**이 fixture가 autouse인 전체 테스트 스위트가 전부 깨진다**:

```python
@pytest.fixture(autouse=True)
def _mock_generate_ai_reply(monkeypatch):
    """모든 리뷰(no_issue 포함)가 generate_ai_reply(RAG)를 타므로(2026-08-24),
    실제 Claude API를 호출하지 않도록 기본값으로 monkeypatch한다. 생성 결과
    자체를 검증하려는 테스트는 monkeypatch.setattr(reviews_mod,
    "generate_ai_reply_with_trace", ...)를 테스트 본문에서 다시 호출해 이
    기본값을 덮어쓸 수 있다(같은 monkeypatch 인스턴스라 나중 호출이
    우선). 반환값이 튜플인 이유: 2026-10-07부터 reviews.py가
    generate_ai_reply_with_trace(content, trace_id)를 쓴다(LangSmith
    연동 플랜, app/llm/generate.py)."""
    from app.routers import reviews as reviews_mod

    monkeypatch.setattr(
        reviews_mod, "generate_ai_reply_with_trace",
        lambda db, review, store, style: (
            f"{review.customer_nickname}님 감사합니다! (테스트 기본 응답)",
            "00000000-0000-0000-0000-000000000000",
        ),
    )
```

- [ ] **Step 8: `test_reviews.py`에 남아있는 다른 `generate_ai_reply` 몽키패치
      5곳도 함께 고친다**

`test_reviews.py`에는 autouse fixture 말고도 테스트 본문에서 직접
`monkeypatch.setattr(reviews_mod, "generate_ai_reply", ...)`를 호출하는
곳이 5군데 더 있다 — Step 7에서 `reviews.py`가 그 이름을 더 이상
import하지 않게 바꿨으므로, 이 5곳을 안 고치면 전부
`AttributeError: <module 'app.routers.reviews'> does not have the
attribute 'generate_ai_reply'`로 깨진다. 전부 "patch 대상 이름"과
"반환값 모양(튜플)"만 바꾸면 되고, 테스트의 의도/assertion은 그대로다:

`:204` (`test_generate_reply_uses_rag_path_for_no_issue_review` 근처)와
`:228`(`test_generate_reply_uses_ai_path_for_problem_review`):

```python
monkeypatch.setattr(reviews_mod, "generate_ai_reply_with_trace", lambda db, review, store, style: ("AI가 만든 답글입니다.", "00000000-0000-0000-0000-000000000000"))
```

`:259`(`test_generate_reply_returns_503_with_korean_error_when_ai_generation_fails`)
— `_raise` 함수 자체(`:256-257`)는 그대로 두고 patch 대상 이름만 바꾼다:

```python
monkeypatch.setattr(reviews_mod, "generate_ai_reply_with_trace", _raise)
```

`:398`(`test_generate_reply_tone_overridden_true_when_sensitive`)과
`:423`(`test_generate_reply_tone_overridden_false_when_not_sensitive`):

```python
monkeypatch.setattr(reviews_mod, "generate_ai_reply_with_trace", lambda db, review, store, style: ("답글", "00000000-0000-0000-0000-000000000000"))
```

(다섬 군데 전부 `reviews_mod`가 이미 그 테스트 함수 안에서 import돼 있는지
확인해라 — `:246`처럼 함수 내부에서 `from app.routers import reviews as
reviews_mod`로 import하는 곳도 있다.)

- [ ] **Step 9: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_reviews.py -v`
Expected: PASS(새 테스트 포함, 기존 테스트 전부 그대로 PASS — 특히
`_mock_generate_ai_reply`와 위 5곳을 쓰는 테스트들이 깨지지 않는지 확인).

- [ ] **Step 10: `onboarding.py`가 trace_id를 저장하도록 실패하는 테스트 작성**

`backend/tests/test_llm_onboarding.py`에서 `get_or_create_scenario`를
테스트하는 기존 테스트 근처에(이 파일이 `generate_ai_reply`를 어떻게
monkeypatch하는지 먼저 읽어라) 추가:

```python
def test_get_or_create_scenario_stores_trace_id(db_session, seeded_user, monkeypatch):
    import app.llm.onboarding as onboarding_mod

    store = seeded_user["store"]
    monkeypatch.setattr(onboarding_mod, "generate_virtual_review", lambda category: "가상 리뷰")
    monkeypatch.setattr(
        onboarding_mod, "generate_ai_reply_with_trace",
        lambda db, review, store, style: ("가상 답글", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    scenario = onboarding_mod.get_or_create_scenario(db_session, store, "delivery")

    assert scenario.trace_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
```

- [ ] **Step 11: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_llm_onboarding.py -k "stores_trace_id" -v`
Expected: FAIL — `AttributeError: module 'app.llm.onboarding' has no attribute 'generate_ai_reply_with_trace'`

- [ ] **Step 12: `onboarding.py` 수정**

`backend/app/llm/onboarding.py:16`의

```python
from app.llm.generate import CATEGORY_LABELS, generate_ai_reply
```

을

```python
from app.llm.generate import CATEGORY_LABELS, generate_ai_reply_with_trace
```

로 바꾸고, `get_or_create_scenario` 안의 해당 줄들(`:94-100` 근처)을 다음으로
교체:

```python
    draft_text, trace_id = generate_ai_reply_with_trace(db, fake_review, store, _default_style(db, store))

    scenario = OnboardingScenario(
        store_id=store.id, category=category,
        virtual_review_text=virtual_review_text, draft_text=draft_text, trace_id=trace_id,
        status="pending", created_at=datetime.now(timezone.utc),
    )
```

- [ ] **Step 13: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_llm_onboarding.py -v`
Expected: PASS.

- [ ] **Step 14: 전체 스위트 회귀 확인**

Run: `cd backend && pytest`
Expected: 전부 PASS.

- [ ] **Step 15: 커밋**

```bash
git add backend/app/llm/generate.py backend/app/routers/reviews.py backend/app/llm/onboarding.py backend/tests/
git commit -m "feat: 수동 생성 버튼/훈련카드 초안이 trace_id를 받아 저장하도록 연결"
```

---

### Task 5: `save_final_reply`/`answer_scenario`가 측정을 트리거한다

**Files:**
- Modify: `backend/app/routers/reviews.py:153-197`(`save_final_reply`)
- Modify: `backend/app/routers/reply_onboarding.py:118-140`(`answer_scenario`)
- Test: `backend/tests/test_reviews.py`, `backend/tests/test_reply_onboarding.py`

**Interfaces:**
- Consumes: Task 2의 `trace_id` 컬럼들, Task 3의 `record_draft_feedback_background`.
- Produces: 없음(이 태스크가 측정 파이프라인의 끝 — Task 6이 결과를 읽는다).

- [ ] **Step 1: 실패하는 테스트 작성 — `save_final_reply`**

`backend/tests/test_reviews.py`에서 `save_final_reply`(`/reviews/{id}/reply`)를
테스트하는 기존 테스트들 근처에 추가(기존 테스트가
`refresh_store_style_profile_background`/`compute_golden_example_embedding_background`를
monkeypatch하는 바로 그 지점과 같은 자리 — 이 파일 상단에서 이미 본
패턴을 그대로 따른다):

이 파일 맨 위의 `make_review` 헬퍼를 재사용한다(직접 `Review(...)`를 새로
만들지 마라) — `save_final_reply`는 `status == "answered"`일 때만 막으므로
`make_review`가 만드는 기본 상태("unanswered")로도 두 테스트 모두 충분하다:

```python
def test_save_final_reply_schedules_feedback_when_draft_has_trace_id(client, auth_headers, db_session, seeded_user, platforms, monkeypatch):
    import app.routers.reviews as reviews_mod
    from app.models import ReviewReply

    monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(reviews_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    calls = []
    monkeypatch.setattr(reviews_mod, "record_draft_feedback_background", lambda **kwargs: calls.append(kwargs))

    store = seeded_user["store"]
    review = make_review(db_session, store, platforms, rating=5, content="맛있어요")
    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=None,
        content="AI 초안", trace_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(draft)
    db_session.commit()

    resp = client.post(f"/reviews/{review.id}/reply", json={"content": "고쳐 쓴 최종본"}, headers=auth_headers)

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["trace_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert calls[0]["draft_text"] == "AI 초안"
    assert calls[0]["final_text"] == "고쳐 쓴 최종본"
    assert calls[0]["store_id"] == store.id
    assert calls[0]["category"] == review.category
    assert calls[0]["source_review_id"] == review.id


def test_save_final_reply_skips_feedback_when_no_draft_existed(client, auth_headers, db_session, seeded_user, platforms, monkeypatch):
    import app.routers.reviews as reviews_mod

    monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(reviews_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    calls = []
    monkeypatch.setattr(reviews_mod, "record_draft_feedback_background", lambda **kwargs: calls.append(kwargs))

    store = seeded_user["store"]
    review = make_review(db_session, store, platforms, rating=5, content="맛있어요")

    resp = client.post(f"/reviews/{review.id}/reply", json={"content": "처음부터 직접 썼어요"}, headers=auth_headers)

    assert resp.status_code == 200
    assert calls == []  # 초안이 없었으니 측정할 쌍이 없다
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_reviews.py -k "feedback" -v`
Expected: FAIL — `AttributeError: module 'app.routers.reviews' has no attribute 'record_draft_feedback_background'`

- [ ] **Step 3: `reviews.py` 수정**

`backend/app/routers/reviews.py`에 `from app.llm.feedback import
record_draft_feedback_background` import 추가(다른 `app.llm.*` import들
근처에).

`save_final_reply` 함수(`:164-196`)에서, 골든 예시 승격 if-블록
(`if draft is None or draft.content != reply.content:`) **바로 뒤**,
`db.commit()` **바로 앞**에 추가:

```python
    if draft is not None and draft.trace_id is not None:
        # 초안이 있었고(수동 생성이든 Plan 2의 자동답글 보류든) 그 초안이
        # trace_id를 들고 있으면 — 즉 LangGraph로 생성된 초안이면 — 사장님이
        # 그걸 그대로 썼든 고쳐 썼든 유사도를 측정한다(스펙 4.2절 경로 A,
        # 초안을 그대로 승인한 경우도 "AI가 잘 맞춘" 유의미한 신호다).
        # 응답을 느리게 만들면 안 되므로 BackgroundTasks로 미룬다.
        background_tasks.add_task(
            record_draft_feedback_background,
            trace_id=draft.trace_id, draft_text=draft.content, final_text=reply.content,
            store_id=review.store_id, category=review.category, source_review_id=review.id,
        )
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_reviews.py -v`
Expected: PASS(새 테스트 2개 포함, 기존 테스트 전부 그대로 PASS).

- [ ] **Step 5: 실패하는 테스트 작성 — `answer_scenario`**

`backend/tests/test_reply_onboarding.py`에서 `answer_scenario`
(`/reply-onboarding/scenarios/{id}/answer`)를 테스트하는 기존 테스트들
근처에 추가:

```python
def test_answer_scenario_schedules_feedback_when_trace_id_present(client, auth_headers, db_session, seeded_user, monkeypatch):
    import app.routers.reply_onboarding as onboarding_router

    monkeypatch.setattr(onboarding_router, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(onboarding_router, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    calls = []
    monkeypatch.setattr(onboarding_router, "record_draft_feedback_background", lambda **kwargs: calls.append(kwargs))

    from app.models import OnboardingScenario

    store = seeded_user["store"]
    scenario = OnboardingScenario(
        store_id=store.id, category="delivery", virtual_review_text="배달이 늦었어요",
        draft_text="불편드려 죄송합니다", trace_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    resp = client.post(
        f"/reply-onboarding/scenarios/{scenario.id}/answer",
        json={"content": "고쳐 쓴 답변"}, headers=auth_headers,
    )

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["trace_id"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert calls[0]["draft_text"] == "불편드려 죄송합니다"
    assert calls[0]["final_text"] == "고쳐 쓴 답변"
    assert calls[0]["source_scenario_id"] == scenario.id


def test_answer_scenario_skips_feedback_without_trace_id(client, auth_headers, db_session, seeded_user, monkeypatch):
    import app.routers.reply_onboarding as onboarding_router

    monkeypatch.setattr(onboarding_router, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(onboarding_router, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    calls = []
    monkeypatch.setattr(onboarding_router, "record_draft_feedback_background", lambda **kwargs: calls.append(kwargs))

    from app.models import OnboardingScenario

    store = seeded_user["store"]
    scenario = OnboardingScenario(
        store_id=store.id, category="price", virtual_review_text="가격이 비싸요",
        draft_text="양해 부탁드립니다", trace_id=None,
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    resp = client.post(
        f"/reply-onboarding/scenarios/{scenario.id}/answer",
        json={"content": "답변"}, headers=auth_headers,
    )

    assert resp.status_code == 200
    assert calls == []
```

- [ ] **Step 6: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_reply_onboarding.py -k "feedback" -v`
Expected: FAIL — `AttributeError: module 'app.routers.reply_onboarding' has no attribute 'record_draft_feedback_background'`

- [ ] **Step 7: `reply_onboarding.py` 수정**

`backend/app/routers/reply_onboarding.py`에 `from app.llm.feedback import
record_draft_feedback_background` import 추가.

`answer_scenario` 함수(`:123-140`)를 다음으로 교체:

```python
    scenario = _get_owned_scenario(db, scenario_id, user)
    if scenario.status == "answered":
        raise HTTPException(409, "이미 답변한 시나리오입니다")

    # 온보딩은 사장님이 직접 검토·제출한 것이므로, save_final_reply(코어
    # 설계)의 초안-대조 승격 판정과 달리 diff 비교 없이 항상 승격한다.
    example = GoldenExample(
        store_id=scenario.store_id, category=scenario.category,
        review_text=scenario.virtual_review_text, reply_text=body.content,
        is_manual=True, is_synthetic=False, source="onboarding",
        created_at=datetime.now(timezone.utc),
    )
    db.add(example)
    scenario.status = "answered"
    db.commit()
    background_tasks.add_task(refresh_store_style_profile_background, scenario.store_id)
    background_tasks.add_task(compute_golden_example_embedding_background, example.id)
    if scenario.trace_id is not None:
        # 경로 B 측정(스펙 4.2절) — 훈련카드 AI초안과 사장님 실제 답변의
        # 유사도. trace_id가 없는 건(이 마이그레이션 이전에 만들어진
        # 시나리오) 측정 대상에서 자연히 빠진다. 기존 코드가 commit 뒤에도
        # scenario.store_id/example.id를 그대로 읽는 것과 같은 패턴이라
        # 별도로 값을 미리 뽑아둘 필요 없다.
        background_tasks.add_task(
            record_draft_feedback_background,
            trace_id=scenario.trace_id, draft_text=scenario.draft_text, final_text=body.content,
            store_id=scenario.store_id, category=scenario.category, source_scenario_id=scenario.id,
        )
    return _row(scenario)
```

- [ ] **Step 8: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_reply_onboarding.py -v`
Expected: PASS(새 테스트 2개 포함, 기존 테스트 전부 그대로 PASS).

- [ ] **Step 9: 전체 스위트 회귀 확인**

Run: `cd backend && pytest`
Expected: 전부 PASS.

- [ ] **Step 10: 커밋**

```bash
git add backend/app/routers/reviews.py backend/app/routers/reply_onboarding.py backend/tests/test_reviews.py backend/tests/test_reply_onboarding.py
git commit -m "feat: save_final_reply/answer_scenario가 초안-최종본 유사도 측정을 트리거한다"
```

---

### Task 6: 대시보드 — 카테고리별 추세 API + 카드 UI

**Files:**
- Modify: `backend/app/routers/dashboard.py`
- Modify: `frontend/src/app/(app)/dashboard/page.tsx`
- Test: `backend/tests/test_dashboard.py`

**Interfaces:**
- Consumes: Task 2의 `DraftFeedbackScore` 모델(이 테이블에 실제로 쌓인 데이터는
  Task 5가 돌면서부터 생긴다 — 이 태스크 자체는 빈 테이블에서도 정상 동작해야
  한다).
- Produces: `GET /dashboard/draft-feedback-trend?store_id=` — `{"categories":
  [{"category": str, "label": str, "avg_similarity": float, "sample_count": int}, ...]}`.

- [ ] **Step 1: 실패하는 테스트 작성 — 백엔드**

`backend/tests/test_dashboard.py`에서 `/dashboard` 엔드포인트를 테스트하는
기존 테스트들 근처에 추가(이 파일이 `store_id` 쿼리 파라미터를 테스트에서
어떻게 넘기는지 먼저 읽어라):

```python
def test_draft_feedback_trend_aggregates_by_category(client, auth_headers, db_session, seeded_user):
    from app.models import DraftFeedbackScore

    store = seeded_user["store"]
    db_session.add_all([
        DraftFeedbackScore(
            store_id=store.id, category="food_quality", similarity_score=0.9,
            trace_id="d1111111-1111-1111-1111-111111111111",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=store.id, category="food_quality", similarity_score=0.7,
            trace_id="d2222222-2222-2222-2222-222222222222",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=store.id, category="no_issue", similarity_score=0.95,
            trace_id="d3333333-3333-3333-3333-333333333333",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    resp = client.get(f"/dashboard/draft-feedback-trend?store_id={store.id}", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    by_category = {c["category"]: c for c in body["categories"]}
    assert by_category["food_quality"]["sample_count"] == 2
    assert by_category["food_quality"]["avg_similarity"] == pytest.approx(0.8, abs=0.001)
    assert by_category["food_quality"]["label"] == "음식 품질(맛/온도/양)"
    assert by_category["no_issue"]["sample_count"] == 1
    assert by_category["no_issue"]["label"] == "특이 불만 없음"


def test_draft_feedback_trend_empty_when_no_scores(client, auth_headers, seeded_user):
    store = seeded_user["store"]
    resp = client.get(f"/dashboard/draft-feedback-trend?store_id={store.id}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"categories": []}
```

`import pytest`가 이 테스트 파일 맨 위에 이미 있는지 확인해라(없으면
추가).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_dashboard.py -k "draft_feedback_trend" -v`
Expected: FAIL — `404 Not Found`(엔드포인트가 아직 없음)

- [ ] **Step 3: `dashboard.py`에 엔드포인트 추가**

`backend/app/routers/dashboard.py`의 import 블록을 다음으로 교체:

```python
from app.acos import calculate_performance
from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.generate import CATEGORY_LABELS
from app.models import (
    AdCampaign,
    AdPerformanceMetric,
    Alert,
    DailySettlement,
    DraftFeedbackScore,
    RepurchaseMetric,
    Review,
    Store,
)

router = APIRouter(tags=["dashboard"])

_NO_ISSUE_LABEL = "특이 불만 없음"
```

파일 맨 끝(`list_alerts` 함수 다음)에 새 엔드포인트 추가:

```python
@router.get("/dashboard/draft-feedback-trend")
def draft_feedback_trend(store_id: int | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """AI 초안과 사장님 최종본의 유사도를 카테고리별로 집계한다(스펙
    4.2절 "순환 측정 장치") — 요약을 별도 테이블로 캐싱하지 않고 매
    조회마다 draft_feedback_scores를 직접 집계한다(daily_settlements와
    같은 정규화 원칙). 표본이 전혀 없는 카테고리는 응답에서 뺀다(0건을
    0.0으로 보여주면 "측정됐는데 낮다"로 오해할 수 있다)."""
    sid = store_id or get_user_default_store_id(user, db)
    rows = db.execute(
        select(
            DraftFeedbackScore.category,
            func.avg(DraftFeedbackScore.similarity_score),
            func.count(DraftFeedbackScore.id),
        )
        .where(DraftFeedbackScore.store_id == sid)
        .group_by(DraftFeedbackScore.category)
        .order_by(func.count(DraftFeedbackScore.id).desc())
    ).all()
    return {
        "categories": [
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, _NO_ISSUE_LABEL if category == "no_issue" else category),
                "avg_similarity": round(float(avg_score), 4),
                "sample_count": count,
            }
            for category, avg_score, count in rows
        ],
    }
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_dashboard.py -v`
Expected: PASS(새 테스트 2개 포함, 기존 테스트 전부 그대로 PASS).

- [ ] **Step 5: 전체 백엔드 스위트 회귀 확인**

Run: `cd backend && pytest`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋(백엔드 부분)**

```bash
git add backend/app/routers/dashboard.py backend/tests/test_dashboard.py
git commit -m "feat: 대시보드에 AI초안-최종본 유사도 카테고리별 집계 API 추가"
```

- [ ] **Step 7: 프론트엔드 — 타입/상태/fetch 추가**

`frontend/src/app/(app)/dashboard/page.tsx`의 타입 선언부(`BreakdownRow`
타입 다음)에 추가:

```tsx
type DraftFeedbackCategory = { category: string; label: string; avg_similarity: number; sample_count: number };
type DraftFeedbackTrendResponse = { categories: DraftFeedbackCategory[] };
```

`DashboardPage` 컴포넌트 안, 기존 `alerts` state 선언 바로 뒤에 추가:

```tsx
  const [feedbackTrend, setFeedbackTrend] = useState<DraftFeedbackTrendResponse | null>(null);
```

기존 `/dashboard`+`/alerts`를 함께 불러오는 `useEffect`(`storeId`,
`baeminPlatformId`에 의존하는 블록) 안에 한 줄 추가:

```tsx
    apiGet<DraftFeedbackTrendResponse>(`/dashboard/draft-feedback-trend?store_id=${storeId}`).then(setFeedbackTrend);
```

(이 호출은 `platform_id`를 받지 않는 엔드포인트라 — Task 6 Step 3에서
`platform_id` 파라미터를 안 받게 만들었다 — `baeminPlatformId`에 의존할
필요는 없지만, 같은 `useEffect` 블록 안에 두면 코드가 흩어지지 않는다.
이 `useEffect`가 이미 `storeId`가 있을 때만 실행되는 guard 안에 있으니
그대로 그 안에 넣어라.)

- [ ] **Step 8: 프론트엔드 — 카드 UI 추가**

기존 "답글 대기 리뷰"/"알림" 2-col 그리드(`<div className="grid
grid-cols-1 gap-4 lg:grid-cols-2">...</div>`) **바로 다음**, `{openModal
=== "ugacle" && (...)}` 블록 **이전**에 새 섹션 추가:

```tsx
      <Card title="AI 초안 학습 추세">
        <p className="mb-3 text-xs text-muted">
          AI가 제안한 초안과 사장님이 실제로 등록한 최종 답글이 얼마나
          비슷한지 카테고리별로 보여줍니다. 높을수록 AI가 사장님 말투에
          가깝게 쓰고 있다는 뜻이에요.
        </p>
        {feedbackTrend === null || feedbackTrend.categories.length === 0 ? (
          <p className="text-sm text-muted">아직 측정된 데이터가 없습니다.</p>
        ) : (
          <ul className="space-y-3">
            {feedbackTrend.categories.map((c) => (
              <li key={c.category}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="text-foreground">{c.label}</span>
                  <span className="text-muted">
                    {(c.avg_similarity * 100).toFixed(1)}% · {c.sample_count}건
                  </span>
                </div>
                <div className="h-2 rounded-full bg-surface-2">
                  <div
                    className="h-2 rounded-full bg-accent"
                    style={{ width: `${Math.max(0, Math.min(100, c.avg_similarity * 100))}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
```

- [ ] **Step 9: 개발 서버로 수동 확인**

Run: `cd frontend && npm run dev`(이미 실행 중이 아니면)

브라우저에서 `/dashboard`를 열어 새 "AI 초안 학습 추세" 카드가 렌더링되는지
확인한다. `draft_feedback_scores`가 아직 빈 테이블이면 "아직 측정된
데이터가 없습니다."가 보이는 게 정상이다 — 에러 없이 그 문구가 뜨는지,
레이아웃이 다른 카드들과 깨지지 않는지만 확인하면 된다.

- [ ] **Step 10: 커밋(프론트엔드 부분)**

```bash
git add "frontend/src/app/(app)/dashboard/page.tsx"
git commit -m "feat: 대시보드에 AI초안 학습 추세 카드 추가"
```

---

## 전체 플랜 완료 후 확인할 것 (사람이 검토)

- `LANGSMITH_API_KEY`를 실제로 발급받아 `.env`/Railway/크롤 워커에
  설정하기 전까지는 트레이싱/feedback 기록이 전부 조용히 스킵된다(의도된
  동작) — 실제로 LangSmith 대시보드에서 trace가 보이는지 확인하려면 키를
  설정한 뒤 수동으로 답글을 한 건 생성+저장해봐야 한다.
- Plan 1(Alembic 0001/0002)과 이번 0003 둘 다 아직 운영 DB에 반영 안 됨 —
  운영 배포 시 `alembic upgrade head`로 한 번에 올라간다(새 시드/백필
  스크립트는 이 플랜에 없다, 0003은 nullable 컬럼 추가 + 신규 테이블뿐).
