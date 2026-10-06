# 리뷰 답글 에이전트 — 메모리 구조 (Plan 1/4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리뷰 답글 AI 에이전트 재설계(`docs/superpowers/specs/2026-10-03-ai-agent-llmops-reply-design.md`)의 1절(메모리 구조) 전체를 구현한다 — 절차기억(`procedural_rules`), 의미기억 신규 소스(`brand_ceo_notices`), 일화기억 신규 진입경로(경로 C)와 3단계 우선순위 리트리벌.

**Architecture:** 하드코딩된 프롬프트 지시문을 DB 테이블(`procedural_rules`)로 분리하고, 사장님공지를 새 스크래퍼(`baemin_notices.py`)로 가져와 `brand_ceo_notices`에 저장하며, `review_sync.py`가 배민에 이미 달린 사장님 답글을 golden_example로 승격하는 새 경로를 추가한다. `fetch_golden_examples`를 `is_manual`/`is_synthetic` 기반에서 `source` 컬럼 기반 3단계(고신뢰organic/일반organic/onboarding) 우선순위로 재작성하고, 더 이상 설계에 없는 `synthetic` 소스(별도 AI 증강, 명시적으로 채택 안 함)는 이번에 제거한다. 전부 기존 Playwright `page.on("response")` 가로채기, pgvector 코사인 거리, 백그라운드 태스크 패턴을 그대로 따른다 — 새 인프라는 추가하지 않는다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL + pgvector, Playwright, Voyage AI 임베딩(`app/llm/embedding.py`), pytest.

## Global Constraints

- `procedural_rules`는 전역 테이블이다 — `store_id` 컬럼을 두지 않는다(스펙 1.1절).
- 조건 판단(언제 어떤 규칙을 적용할지)은 Python 코드에 남기고, DB에서는 `instruction_text`만 조회한다 — 규칙 엔진을 만들지 않는다(스펙 1.1절).
- `brand_ceo_notices`는 매 동기화마다 긁지 않는다 — `brand_menu_info`와 동일하게 30일 스킵, 동기화 시 전체 교체(snapshot replace) 방식이다(스펙 1.2절).
- golden_examples 신뢰도 신호(리뷰수≤3/불만카테고리/sentiment_conflict)는 컬럼으로 저장하지 않고 조회 시점에 `reviews`와 JOIN해서 계산한다(스펙 1.4절, 정규화 원칙).
- `source="synthetic"` 메커니즘(`backend/scripts/seed_synthetic_golden_examples.py`)은 이번에 완전히 제거한다 — CLAUDE.md에 이미 "명시적으로 채택 안 함"으로 기록된 접근이고, 새 3단계 설계에 들어갈 자리가 없다.
- 1.4.1절의 임베딩 일관성 체크(경로 C 전용)는 **저장을 막지 않는다** — 이상치여도 golden_example은 항상 저장하고, 플래그만 남긴다. 사장님 확인 UI는 이 플랜 범위 밖이다(Plan 4, DeepTwin UI에서 다룬다).
- pgvector(`<->`/`cosine_distance`)를 실행하는 로직은 SQLite 유닛 테스트에서 검증할 수 없다 — 기존 관례대로 `tests/test_llm_rag_pgvector.py`(로컬 Postgres 필요, 없으면 자동 스킵)에 추가한다.

---

### Task 1: `procedural_rules` 테이블 + 시드 데이터

**Files:**
- Modify: `schema.sql`
- Modify: `backend/app/models.py`
- Create: `backend/scripts/seed_procedural_rules.py`
- Test: `backend/tests/test_seed_procedural_rules.py`

**Interfaces:**
- Produces: `ProceduralRule` 모델(`rule_key`, `instruction_text`, `active`, `description`), `seed_procedural_rules.run(db: Session) -> None`(멱등 — 이미 있는 rule_key는 건드리지 않고 없는 것만 추가).

- [ ] **Step 1: schema.sql에 테이블 추가**

`golden_examples` 테이블 정의 바로 앞(파일에서 테이블이 생성 순서대로 나열된 구간 아무 곳이나, 기존 스타일을 따라 알파벳/의존성 순서와 무관하게 논리적으로 묶이는 자리)에 추가:

```sql
CREATE TABLE procedural_rules (
    id              BIGSERIAL PRIMARY KEY,
    rule_key        VARCHAR(40)  NOT NULL UNIQUE,
    instruction_text TEXT        NOT NULL,
    active          BOOLEAN      NOT NULL DEFAULT true,
    description     TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: models.py에 모델 추가**

`backend/app/models.py`의 `GoldenExample` 클래스 정의 바로 앞에 추가(관련 있는 LLM 설정 모델끼리 묶이도록):

```python
class ProceduralRule(Base):
    __tablename__ = "procedural_rules"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(40), unique=True)
    instruction_text: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
```

(파일 상단에 `datetime`은 이미 import돼 있다 — 다른 모델들이 쓰고 있으므로 추가 import 불필요.)

- [ ] **Step 3: 실패하는 테스트 작성**

```python
from app.models import ProceduralRule
from scripts.seed_procedural_rules import run


def test_seed_procedural_rules_creates_all_six(db_session):
    run(db_session)
    db_session.commit()

    rules = {r.rule_key: r for r in db_session.query(ProceduralRule).all()}
    assert set(rules) == {
        "complaint_tone_override", "few_shot_anti_overfit", "menu_grounding",
        "no_issue_framing", "delivery_boundary",
    }
    assert all(r.active for r in rules.values())


def test_seed_procedural_rules_is_idempotent(db_session):
    run(db_session)
    run(db_session)  # 두 번 실행해도 중복 안 생김
    db_session.commit()

    count = db_session.query(ProceduralRule).count()
    assert count == 5
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_seed_procedural_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.seed_procedural_rules'`

- [ ] **Step 5: 시드 스크립트 작성**

```python
"""procedural_rules 초기 데이터 — 기존에 backend/app/llm/generate.py에
하드코딩돼 있던 지시문 4개 + 신규 delivery 경계 규칙 1개를 DB로 옮긴다
(스펙 2026-10-03-ai-agent-llmops-reply-design.md 1.1절). rule_key가 이미
있으면 건드리지 않는다(멱등) — 운영 중 사람이 instruction_text를 고쳐둔
걸 재실행이 덮어쓰면 안 된다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProceduralRule

_RULES: dict[str, tuple[str, str]] = {
    "complaint_tone_override": (
        "불만이 담긴 리뷰입니다(위생/안전 문제이거나 별점과 내용이 어긋나는 "
        "경우 포함). 페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 "
        "작성하세요.",
        "불만 리뷰(category != no_issue, is_sensitive, sentiment_conflict)에 적용되는 톤 강제 규칙",
    ),
    "few_shot_anti_overfit": (
        "이 예시들은 말투·태도·구조(원인 설명 → 사과 → 재방문 유도)만 "
        "참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은 반드시 "
        "\"이번 리뷰의 실제 상황\"에만 근거해 새로 작성하라.",
        "few-shot 예시 과적합(문장 그대로 복사) 방지 지시",
    ),
    "menu_grounding": (
        "리뷰가 특정 메뉴나 재료를 언급하면 반드시 이 정보를 근거로 삼아라 "
        "— 실제 메뉴 구성과 다른 원인(예: \"신메뉴라서\", \"양을 줄였다\")을 "
        "추측해서 쓰지 마라. 여기 없는 내용(오늘 그 배치의 조리 상태 등)은 "
        "사장님만 아는 사실이니 지어내지 말고 일반적인 사과로 넘어가라.",
        "brand_menu_info 그라운딩 데이터 사용 지시",
    ),
    "no_issue_framing": (
        "특이 불만 없음(칭찬 또는 중립적인 리뷰). 리뷰에 구체적인 취향/요청이 "
        "담겨있으면 자연스럽게 반영하고, 없으면 감사 인사 위주로 답하세요.",
        "category == no_issue 리뷰의 프레이밍",
    ),
    "delivery_boundary": (
        "배달 지연·파손·라이더(배달원) 응대 등 가게가 직접 통제할 수 없는 "
        "배달 과정 불만에는 공감과 유감을 표현하되, 가게가 전적으로 "
        "책임지거나 무조건 '수정하겠습니다'라고 약속하지 않는다. 가게 "
        "책임이 아님을 부드럽게 알리면서도 고객 불편엔 공감하는 균형을 "
        "유지하세요.",
        "category == delivery 리뷰 전용 — 가게 통제 밖 불만의 책임 경계 (신규, 2026-10-06)",
    ),
}


def run(db: Session) -> None:
    existing = set(db.scalars(select(ProceduralRule.rule_key)).all())
    for key, (text, description) in _RULES.items():
        if key in existing:
            continue
        db.add(ProceduralRule(
            rule_key=key, instruction_text=text, active=True,
            description=description, created_at=datetime.now(timezone.utc),
        ))


if __name__ == "__main__":
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        run(session)
        session.commit()
    finally:
        session.close()
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_seed_procedural_rules.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add schema.sql backend/app/models.py backend/scripts/seed_procedural_rules.py backend/tests/test_seed_procedural_rules.py
git commit -m "feat: procedural_rules 테이블 + 시드 데이터 추가"
```

---

### Task 2: generate.py를 procedural_rules 조회로 리팩터링

**Files:**
- Modify: `backend/app/llm/generate.py:1-258`
- Test: `backend/tests/test_llm_generate.py`

**Interfaces:**
- Consumes: `ProceduralRule`(Task 1), `backend/app/llm/generate.py`의 기존 `_build_system_prompt`/`_build_user_message`/`generate_ai_reply`.
- Produces: `fetch_active_rules(db: Session) -> dict[str, str]` — `rule_key`→`instruction_text`, `active=True`인 것만.

- [ ] **Step 1: 실패하는 테스트 작성**

기존 `test_llm_generate.py`는 하드코딩 상수를 직접 검증하고 있을 가능성이 높다 — procedural_rules 기반으로 바뀌는 걸 검증하는 새 테스트를 추가한다. 테스트 DB에 규칙을 심어두고 그 텍스트가 실제로 프롬프트에 꽂히는지 확인한다:

```python
from datetime import date, datetime, timezone

from app.llm.generate import generate_ai_reply
from app.models import BaeminShopBrand, ProceduralRule, ReplyStyle, Review, Store, StorePlatformConnection, Subscription


def _seed_rule(db_session, key, text):
    db_session.add(ProceduralRule(
        rule_key=key, instruction_text=text, active=True,
        created_at=datetime.now(timezone.utc),
    ))


def test_generate_ai_reply_uses_procedural_rule_text_for_complaint_tone(
    db_session, seeded_user, reply_styles, monkeypatch,
):
    _seed_rule(db_session, "complaint_tone_override", "테스트용 불만 톤 규칙 문구입니다.")
    _seed_rule(db_session, "few_shot_anti_overfit", "테스트용 복붙 금지 문구.")
    _seed_rule(db_session, "menu_grounding", "테스트용 메뉴 그라운딩 문구.")
    _seed_rule(db_session, "no_issue_framing", "테스트용 무난 프레이밍 문구.")
    _seed_rule(db_session, "delivery_boundary", "테스트용 배달 경계 문구.")
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=1, menu_summary="치킨", rating=1,
        content="배달이 너무 늦었어요", customer_nickname="손님",
        category="delivery", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system_prompt, user_message, max_tokens):
        captured["system_prompt"] = system_prompt
        return "테스트 응답"

    monkeypatch.setattr("app.llm.generate.client.call_sonnet", _fake_call_sonnet)

    generate_ai_reply(db_session, review, store, reply_styles)

    assert "테스트용 불만 톤 규칙 문구입니다." in captured["system_prompt"]
    assert "테스트용 복붙 금지 문구." in captured["system_prompt"]
    assert "테스트용 배달 경계 문구." in captured["system_prompt"]


def test_generate_ai_reply_skips_inactive_rule(db_session, seeded_user, reply_styles, monkeypatch):
    db_session.add(ProceduralRule(
        rule_key="complaint_tone_override", instruction_text="비활성 문구",
        active=False, created_at=datetime.now(timezone.utc),
    ))
    store = seeded_user["store"]
    review = Review(
        store_id=store.id, platform_id=1, menu_summary="치킨", rating=1,
        content="맛이 없어요", customer_nickname="손님",
        category="food_quality", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        "app.llm.generate.client.call_sonnet",
        lambda system_prompt, user_message, max_tokens: (captured.__setitem__("p", system_prompt), "응답")[1],
    )

    generate_ai_reply(db_session, review, store, reply_styles)

    assert "비활성 문구" not in captured["p"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_generate.py -k "procedural_rule or inactive_rule" -v`
Expected: FAIL — `"테스트용 불만 톤 규칙 문구입니다." in captured["system_prompt"]`가 False(기존 하드코딩 문구가 대신 들어있음)

- [ ] **Step 3: generate.py 수정**

`_COMPLAINT_TONE_OVERRIDE` 상수와 그 위 import 구간을 수정:

```python
from app.models import BaeminShopBrand, BrandMenuInfo, ProceduralRule, ReplyStyle, Review, Store, StorePlatformConnection, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_FALLBACK_RULES = {
    "complaint_tone_override": (
        "불만이 담긴 리뷰입니다. 페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."
    ),
    "few_shot_anti_overfit": (
        "예시는 말투만 참고하고 문장을 그대로 복사하지 마라."
    ),
    "menu_grounding": "리뷰가 특정 메뉴/재료를 언급하면 실제 메뉴 정보를 근거로 삼아라.",
    "no_issue_framing": "특이 불만 없음. 감사 인사 위주로 답하세요.",
    "delivery_boundary": "배달 과정 불만에는 공감하되 가게 책임으로 단정하지 마세요.",
}


def fetch_active_rules(db: Session) -> dict[str, str]:
    """procedural_rules에서 active=True인 규칙만 rule_key→instruction_text로
    가져온다. DB에 아직 시드가 안 된 rule_key는 _FALLBACK_RULES로
    보강한다 — seed_procedural_rules.py를 안 돌린 로컬/테스트 환경에서도
    답글 생성 자체가 완전히 깨지지 않게 하기 위한 최소 안전장치다."""
    rows = db.execute(select(ProceduralRule.rule_key, ProceduralRule.instruction_text).where(ProceduralRule.active.is_(True))).all()
    rules = dict(_FALLBACK_RULES)
    rules.update({key: text for key, text in rows})
    return rules
```

(`_EMOJI_PATTERN`/`_strip_emoji`/`CATEGORY_LABELS`/`_resolve_display_name`/`_normalize_menu_name`/`_find_menu_context`는 그대로 둔다 — 이 태스크 범위 밖.)

`_build_system_prompt` 시그니처와 본문 수정(과적합 방지 문구·메뉴 그라운딩 안내 문구를 파라미터로 받도록):

```python
def _build_system_prompt(
    display_name: str, style_rules: str, examples, tone_instruction: str,
    rules: dict[str, str], menu_context: str | None = None, *,
    strip_example_emoji: bool = False,
) -> str:
    def _example_reply(ex) -> str:
        return _strip_emoji(ex.reply_text) if strip_example_emoji else ex.reply_text

    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{_example_reply(ex)}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    menu_section = f"""

[가게/메뉴 실제 정보 — 사실 근거용]
{rules["menu_grounding"]}

{menu_context}""" if menu_context else ""

    return f"""너는 "{display_name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[답글 톤]
{tone_instruction}
{menu_section}
[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: {rules["few_shot_anti_overfit"]}

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""
```

`_build_user_message` 시그니처와 본문 수정(no_issue 프레이밍을 파라미터로):

```python
def _build_user_message(review: Review, category_label: str, repeat_count: int, rules: dict[str, str]) -> str:
    lines = [f"별점: {review.rating}"]
    if review.category == "no_issue":
        lines.append(rules["no_issue_framing"])
    else:
        lines.append(f"불만 유형: {category_label}")
    lines.append(f'내용: "{review.content}"')
    lines.append(f"이 고객의 누적 주문 횟수: {review.customer_order_count}회")
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if review.category != "no_issue" and repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)
```

`generate_ai_reply` 수정(규칙 조회 + delivery_boundary 적용, Task 3에서 조건은 그대로 두고 여기서는 배선만):

```python
def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
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
    system_prompt = _build_system_prompt(
        display_name, style_rules, examples, tone_instruction, rules, menu_context,
        strip_example_emoji=tone_overridden,
    )
    user_message = _build_user_message(review, category_label, repeat_count, rules)
    content = client.call_sonnet(system_prompt, user_message, max_tokens=800)
    return _strip_emoji(content) if tone_overridden else content
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_generate.py -v`
Expected: PASS 전체(기존 테스트 포함 — 기존 테스트가 하드코딩 문구를 문자열로 직접 검증하던 게 있다면, 그 부분만 `_FALLBACK_RULES`의 해당 문구로 교체해서 통과시킨다. "Similar to Task N" 없이 실제로 하나씩 열어서 확인·수정할 것)

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/generate.py backend/tests/test_llm_generate.py
git commit -m "refactor: generate.py 하드코딩 지시문을 procedural_rules 조회로 교체"
```

---

### Task 3: delivery 카테고리 범위 확장

**Files:**
- Modify: `backend/app/llm/classify.py:15`
- Test: `backend/tests/test_llm_classify.py` (파일 없으면 새로 생성)

**Interfaces:**
- Consumes: 없음(독립).
- Produces: 없음(분류 프롬프트 텍스트만 변경, 시그니처 변화 없음).

- [ ] **Step 1: 실패하는 테스트 작성**

분류 자체는 Haiku 호출이라 결정론적으로 테스트할 수 없다 — 프롬프트 문자열에 "라이더"가 포함되는지만 확인한다(실제 분류 품질은 코드 테스트 범위 밖, 프롬프트가 올바른 지시를 담고 있는지만 보장):

```python
from app.llm.classify import _SYSTEM_PROMPT


def test_delivery_category_description_includes_rider():
    assert "라이더" in _SYSTEM_PROMPT
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_classify.py -v`
Expected: FAIL — `assert "라이더" in _SYSTEM_PROMPT`

- [ ] **Step 3: classify.py 수정**

`_SYSTEM_PROMPT`의 `delivery` 줄을 교체:

```python
- delivery: 배달 지연, 파손, 배달원(라이더) 응대 등 배달 과정에서 발생한 불만 (가게가 통제 가능한 응대 문제는 service로)
```

(`service` 줄도 "가게가 통제 가능한" 경계를 분명히 하도록 함께 수정)

```python
- service: 가게 직원의 응대, 태도에 대한 불만 (배달원 응대는 delivery로)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_classify.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/classify.py backend/tests/test_llm_classify.py
git commit -m "fix: delivery 카테고리 설명에 라이더 응대 포함 (service와 경계 명확화)"
```

---

### Task 4: `brand_ceo_notices` 테이블

**Files:**
- Modify: `schema.sql`
- Modify: `backend/app/models.py`

**Interfaces:**
- Produces: `BrandCeoNotice` 모델(`connection_id`, `shop_no`, `external_notice_id`, `contents`, `display_status`, `block_type`, `notice_created_at`, `synced_at`).

- [ ] **Step 1: schema.sql에 테이블 추가**

`brand_menu_info` 테이블 정의 바로 뒤에 추가:

```sql
CREATE TABLE brand_ceo_notices (
    id                 BIGSERIAL PRIMARY KEY,
    connection_id      BIGINT       NOT NULL REFERENCES store_platform_connections(id) ON DELETE CASCADE,
    shop_no            VARCHAR(20)  NOT NULL,
    external_notice_id BIGINT       NOT NULL,
    contents           TEXT         NOT NULL,
    display_status     VARCHAR(16)  NOT NULL,
    block_type         VARCHAR(16)  NOT NULL,
    notice_created_at  TIMESTAMPTZ  NOT NULL,
    synced_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_brand_ceo_notices_lookup ON brand_ceo_notices(connection_id, shop_no);
```

(히스토리를 안 남기고 매 동기화마다 전체 교체하는 테이블이라 `UNIQUE(connection_id, shop_no, external_notice_id)` 제약을 굳이 걸지 않는다 — 동기화 로직이 항상 그 (connection_id, shop_no) 행을 먼저 지우고 다시 넣으므로 중복이 구조적으로 생기지 않는다.)

- [ ] **Step 2: models.py에 모델 추가**

`BrandMenuInfo` 클래스 바로 뒤에 추가:

```python
class BrandCeoNotice(Base):
    __tablename__ = "brand_ceo_notices"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("store_platform_connections.id"))
    shop_no: Mapped[str] = mapped_column(String(20))
    external_notice_id: Mapped[int] = mapped_column(BigInteger)
    contents: Mapped[str] = mapped_column(Text)
    display_status: Mapped[str] = mapped_column(String(16))
    block_type: Mapped[str] = mapped_column(String(16))
    notice_created_at: Mapped[datetime]
    synced_at: Mapped[datetime]

    connection: Mapped["StorePlatformConnection"] = relationship()
```

- [ ] **Step 3: 테이블 생성 확인**

Run: `cd backend && python -c "from app.db import Base; from app import models; import sqlalchemy; engine = sqlalchemy.create_engine('sqlite://'); Base.metadata.create_all(engine); print('brand_ceo_notices' in sqlalchemy.inspect(engine).get_table_names())"`
Expected: `True` 출력

- [ ] **Step 4: Commit**

```bash
git add schema.sql backend/app/models.py
git commit -m "feat: brand_ceo_notices 테이블 추가 (사장님공지 저장용)"
```

---

### Task 5: `baemin_notices.py` 스크래퍼

**Files:**
- Create: `backend/scrapers/baemin_notices.py`
- Test: `backend/tests/test_baemin_notices.py`

**Interfaces:**
- Consumes: 없음(Playwright `page` 객체만 받음, `baemin_menu.py`와 동일 패턴).
- Produces: `fetch_ceo_notices(page, shop_no: int) -> list[dict]` — 각 dict는 `{"external_notice_id", "contents", "display_status", "block_type", "notice_created_at"}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime, timezone

from scrapers.baemin_notices import map_notices


def test_map_notices_extracts_display_notices():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": "리뷰 이벤트 공지",
                "images": [], "createdAt": "2025-10-17T13:37:00.14668",
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
        ],
    }

    result = map_notices(raw)

    assert result == [{
        "external_notice_id": 2197877,
        "contents": "리뷰 이벤트 공지",
        "display_status": "DISPLAY",
        "block_type": "NONE",
        "notice_created_at": datetime(2025, 10, 17, 13, 37, 0, 146680, tzinfo=timezone.utc),
    }]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_baemin_notices.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.baemin_notices'`

- [ ] **Step 3: 스크래퍼 작성**

```python
"""배민 사장님광장의 "사장님공지" 화면에서 공지 목록을 가져온다. 리뷰관리와
같은 API 네임스페이스(/v1/review/shops/...)지만 좌측 메뉴의 별도
"사장님공지" 항목을 클릭해야 발생하는 organic 응답이다(실측 확인,
2026-10 — 설계 문서 2026-10-03-ai-agent-llmops-reply-design.md 1.2절
참고). baemin_menu.py와 달리 page.goto() 직접 진입이 아니라 사이드바
클릭이 필요하다는 게 다르다 — "사장님공지"는 메뉴관리 URL 체계 밖의
별도 화면이라 shop_no만으로 URL을 구성할 수 없다(실측 확인된 URL
패턴이 없음)."""

from datetime import datetime, timezone
from urllib.parse import urlparse


class BaeminNoticesScrapeError(Exception):
    pass


def map_notices(raw: dict) -> list[dict]:
    """ceo/notices 응답을 우리 스키마로 변환한다. createdAt은 타임존 없는
    ISO 문자열(예: "2025-10-17T13:37:00.14668")이라 UTC로 간주해 attach한다
    — 배민 API가 돌려주는 시각이 실측상 UTC였다(사장님공지는 리뷰처럼
    한국 벽시계 시간이 아니라 이미 UTC 포맷으로 옴, baemin_reviews.py의
    parse_baemin_datetime과는 다른 포맷이라 재사용하지 않는다)."""
    result = []
    for notice in raw.get("notices", []):
        created_raw = notice.get("createdAt")
        created_at = datetime.fromisoformat(created_raw).replace(tzinfo=timezone.utc) if created_raw else None
        result.append({
            "external_notice_id": notice["id"],
            "contents": notice.get("contents") or "",
            "display_status": notice.get("displayStatus", "DISPLAY"),
            "block_type": notice.get("blockType", "NONE"),
            "notice_created_at": created_at,
        })
    return result


def fetch_ceo_notices(page, shop_no: int) -> list[dict]:
    """로그인된 page로 사이드바의 "사장님공지" 메뉴를 클릭해 공지 목록을
    가져온다. 응답을 못 받으면(레이아웃 변경, 공지가 아예 없는 매장 등)
    빈 리스트를 반환한다 — baemin_menu.py와 달리 "공지 없음"이 정상
    상태일 수 있어(신규 매장은 공지를 안 쓸 수 있음) 하드 에러로 막지
    않는다."""
    captured: dict | None = None

    def _on_response(response) -> None:
        nonlocal captured
        if response.status != 200:
            return
        path = urlparse(response.url).path
        if path == f"/v1/review/shops/{shop_no}/ceo/notices":
            try:
                captured = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.get_by_text("사장님공지", exact=True).click()
        except Exception as e:
            raise BaeminNoticesScrapeError(f"사장님공지 메뉴 진입에 실패했습니다: {e}") from e
        page.wait_for_timeout(3_000)
    finally:
        page.remove_listener("response", _on_response)

    if captured is None:
        return []
    return map_notices(captured)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_baemin_notices.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scrapers/baemin_notices.py backend/tests/test_baemin_notices.py
git commit -m "feat: 사장님공지 스크래퍼(baemin_notices.py) 추가"
```

---

### Task 6: review_sync.py에 사장님공지 동기화 연결 + RAG 주입

**Files:**
- Modify: `backend/app/review_sync.py`
- Modify: `backend/app/llm/generate.py:128-176` (`_find_menu_context`)
- Test: `backend/tests/test_review_sync.py`
- Test: `backend/tests/test_llm_generate.py`

**Interfaces:**
- Consumes: `fetch_ceo_notices`(Task 5), `BrandCeoNotice`(Task 4).
- Produces: `notices_need_refresh(db, connection_id, shop_no) -> bool`, `replace_brand_ceo_notices(db, connection_id, shop_no, notices: list[dict]) -> None`.

- [ ] **Step 1: 실패하는 테스트 작성 (review_sync.py)**

```python
from datetime import datetime, timedelta, timezone

from app.models import BrandCeoNotice
from app.review_sync import notices_need_refresh, replace_brand_ceo_notices


def test_notices_need_refresh_true_when_missing(db_session, seeded_user, platforms):
    conn_id = db_session.query(StorePlatformConnection).first().id
    assert notices_need_refresh(db_session, conn_id, 14804318) is True


def test_notices_need_refresh_false_within_30_days(db_session, seeded_user):
    from app.models import StorePlatformConnection

    conn_id = db_session.query(StorePlatformConnection).first().id
    db_session.add(BrandCeoNotice(
        connection_id=conn_id, shop_no="14804318", external_notice_id=1,
        contents="공지", display_status="DISPLAY", block_type="NONE",
        notice_created_at=datetime.now(timezone.utc),
        synced_at=datetime.now(timezone.utc) - timedelta(days=10),
    ))
    db_session.commit()

    assert notices_need_refresh(db_session, conn_id, 14804318) is False


def test_replace_brand_ceo_notices_replaces_existing(db_session, seeded_user):
    from app.models import StorePlatformConnection

    conn_id = db_session.query(StorePlatformConnection).first().id
    db_session.add(BrandCeoNotice(
        connection_id=conn_id, shop_no="14804318", external_notice_id=999,
        contents="옛날 공지", display_status="DISPLAY", block_type="NONE",
        notice_created_at=datetime.now(timezone.utc), synced_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    replace_brand_ceo_notices(db_session, conn_id, 14804318, [{
        "external_notice_id": 1, "contents": "새 공지",
        "display_status": "DISPLAY", "block_type": "NONE",
        "notice_created_at": datetime.now(timezone.utc),
    }])
    db_session.commit()

    rows = db_session.query(BrandCeoNotice).filter_by(connection_id=conn_id, shop_no="14804318").all()
    assert len(rows) == 1
    assert rows[0].contents == "새 공지"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_review_sync.py -k notices -v`
Expected: FAIL — `ImportError: cannot import name 'notices_need_refresh'`

- [ ] **Step 3: review_sync.py 수정**

`_MENU_INFO_MAX_AGE_DAYS` 상수 바로 아래, `menu_info_needs_refresh`/`upsert_brand_menu_info` 함수 뒤에 추가:

```python
_NOTICES_MAX_AGE_DAYS = 30


def notices_need_refresh(db: Session, connection_id: int, shop_no: int) -> bool:
    """사장님공지도 메뉴 정보와 같은 이유로 자주 안 바뀐다 — 사이드바 이동
    비용을 매번 치르지 않는다(menu_info_needs_refresh와 동일 패턴)."""
    row = db.scalar(
        select(BrandCeoNotice.synced_at).where(
            BrandCeoNotice.connection_id == connection_id,
            BrandCeoNotice.shop_no == str(shop_no),
        ).order_by(BrandCeoNotice.synced_at.desc())
    )
    if row is None:
        return True
    if row.tzinfo is None:
        row = row.replace(tzinfo=timezone.utc)
    return row < datetime.now(timezone.utc) - timedelta(days=_NOTICES_MAX_AGE_DAYS)


def replace_brand_ceo_notices(db: Session, connection_id: int, shop_no: int, notices: list[dict]) -> None:
    """전체 교체(snapshot replace) — 히스토리를 안 남기고 지금 활성 공지만
    유지한다(스펙 1.2절, 이 테이블의 유일한 용도가 "RAG에 넣을 현재 활성
    공지"라서)."""
    db.query(BrandCeoNotice).filter(
        BrandCeoNotice.connection_id == connection_id,
        BrandCeoNotice.shop_no == str(shop_no),
    ).delete()
    now = datetime.now(timezone.utc)
    for notice in notices:
        db.add(BrandCeoNotice(
            connection_id=connection_id, shop_no=str(shop_no),
            external_notice_id=notice["external_notice_id"], contents=notice["contents"],
            display_status=notice["display_status"], block_type=notice["block_type"],
            notice_created_at=notice["notice_created_at"] or now, synced_at=now,
        ))
```

import 구간에 `BrandCeoNotice` 추가, `from scrapers.baemin_notices import BaeminNoticesScrapeError, fetch_ceo_notices` 추가.

`menu_errors` 선언부 뒤, `menu_info_needs_refresh` 블록(377~393행대) 바로 뒤에 같은 패턴으로 추가:

```python
            if notices_need_refresh(db, conn.id, shop_no):
                try:
                    notices = fetch_ceo_notices(session.page, shop_no)
                    replace_brand_ceo_notices(db, conn.id, shop_no, notices)
                except BaeminNoticesScrapeError as e:
                    menu_errors.append(f"{shop_name} 사장님공지: {e}")
```

(기존 `menu_errors` 리스트를 재사용한다 — 사장님공지 실패도 "부분 실패"로 같은 경고 채널에 쌓이면 된다, 별도 리스트를 만들 이유가 없다.)

- [ ] **Step 4: generate.py의 _find_menu_context에 공지 주입**

`_find_menu_context` 함수의 `lines` 조립부를 수정(import에 `BrandCeoNotice` 추가):

```python
    if info is None and not _active_notices(db, store, review):
        return None

    lines = []
    if info is not None:
        if info.store_intro:
            lines.append(f"[가게 소개]\n{info.store_intro}")
        if info.food_origin:
            lines.append(f"[원산지]\n{info.food_origin}")
        if info.menu_intro:
            lines.append(f"[메뉴 소개]\n{info.menu_intro}")

    notice_text = _active_notices(db, store, review)
    if notice_text:
        lines.append(f"[사장님공지]\n{notice_text}")
```

`_find_menu_context` 바로 위에 헬퍼 추가:

```python
def _active_notices(db: Session, store: Store, review: Review) -> str | None:
    """display_status=DISPLAY AND block_type=NONE인 공지만 전부 이어붙인다
    (스펙 1.2절). 여러 개면 개행 두 번으로 구분한다."""
    if not review.platform_shop_no:
        return None
    rows = db.scalars(
        select(BrandCeoNotice)
        .join(StorePlatformConnection, BrandCeoNotice.connection_id == StorePlatformConnection.id)
        .where(
            StorePlatformConnection.store_id == store.id,
            BrandCeoNotice.shop_no == review.platform_shop_no,
            BrandCeoNotice.display_status == "DISPLAY",
            BrandCeoNotice.block_type == "NONE",
        )
    ).all()
    return "\n\n".join(r.contents for r in rows) or None
```

(`_find_menu_context`의 앞부분 `if info is None: return None` 한 줄짜리 early-return은 위 Step의 새 조건으로 교체되므로 삭제한다.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_review_sync.py -k notices tests/test_llm_generate.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/review_sync.py backend/app/llm/generate.py backend/tests/test_review_sync.py backend/tests/test_llm_generate.py
git commit -m "feat: 사장님공지 동기화 연결 + RAG 프롬프트 주입"
```

---

### Task 7: 경로 C — 배민 직접 답글의 golden_examples 승격 + 임베딩 일관성 체크

**Files:**
- Modify: `schema.sql` (golden_examples에 컬럼 추가)
- Modify: `backend/app/models.py` (`GoldenExample`)
- Modify: `backend/app/llm/rag.py`
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_llm_rag_pgvector.py` (임베딩 일관성 체크, Postgres 필요)
- Test: `backend/tests/test_review_sync.py` (승격 배선)

**Interfaces:**
- Consumes: `extract_owner_reply`(기존), `compute_golden_example_embedding`(기존, `rag.py`).
- Produces: `check_voice_consistency(db, store_id, category, candidate_embedding) -> bool | None`(`rag.py`), `promote_direct_reply_to_golden_example(db, review, reply_id, reply_text) -> GoldenExample`(`rag.py`).

- [ ] **Step 1: schema.sql / models.py에 `needs_confirmation` 컬럼 추가**

`golden_examples` 테이블의 `source` CHECK 제약에 `'organic_direct'` 추가, `needs_confirmation` 컬럼 추가:

```sql
    source           VARCHAR(16)  NOT NULL
                     CHECK (source IN ('backfill', 'organic', 'organic_direct', 'onboarding')),
    source_review_id BIGINT       REFERENCES reviews(id) ON DELETE SET NULL,
    source_reply_id  BIGINT       REFERENCES review_replies(id) ON DELETE SET NULL,
    embedding        vector(1024),
    needs_confirmation BOOLEAN    NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
```

(`'synthetic'`은 Task 8에서 제거하므로 CHECK 목록에서 뺀다. `'organic_direct'`가 경로 C — 배민에 직접 단 답글.)

`models.py`의 `GoldenExample`에 필드 추가(`source_reply_id` 다음 줄):

```python
    needs_confirmation: Mapped[bool] = mapped_column(default=False)
```

- [ ] **Step 2: 실패하는 테스트 작성 (임베딩 일관성, Postgres 전용)**

`tests/test_llm_rag_pgvector.py`에 추가(이 파일은 로컬 Postgres 없으면 자동 스킵되는 기존 관례를 따른다 — 상단의 스킵 마커 패턴을 그대로 재사용):

```python
def test_check_voice_consistency_none_when_baseline_insufficient(pg_session, seeded_store_pg):
    from app.llm.rag import check_voice_consistency

    result = check_voice_consistency(pg_session, seeded_store_pg.id, "delivery", [0.1] * 1024)

    assert result is None


def test_check_voice_consistency_true_when_close_to_baseline(pg_session, seeded_store_pg):
    from app.llm.rag import check_voice_consistency
    from app.models import GoldenExample

    base_vec = [0.5] * 1024
    for i in range(3):
        pg_session.add(GoldenExample(
            store_id=seeded_store_pg.id, category="delivery",
            review_text=f"리뷰{i}", reply_text=f"답글{i}",
            is_manual=True, is_synthetic=False, source="organic",
            embedding=base_vec, created_at=datetime.now(timezone.utc),
        ))
    pg_session.commit()

    result = check_voice_consistency(pg_session, seeded_store_pg.id, "delivery", base_vec)

    assert result is True


def test_check_voice_consistency_false_when_outlier(pg_session, seeded_store_pg):
    from app.llm.rag import check_voice_consistency
    from app.models import GoldenExample

    base_vec = [1.0] + [0.0] * 1023
    for i in range(3):
        pg_session.add(GoldenExample(
            store_id=seeded_store_pg.id, category="delivery",
            review_text=f"리뷰{i}", reply_text=f"답글{i}",
            is_manual=True, is_synthetic=False, source="organic",
            embedding=base_vec, created_at=datetime.now(timezone.utc),
        ))
    pg_session.commit()
    outlier_vec = [0.0] * 1023 + [1.0]  # base_vec과 직교(코사인 거리 최대)

    result = check_voice_consistency(pg_session, seeded_store_pg.id, "delivery", outlier_vec)

    assert result is False
```

(정확한 `pg_session`/`seeded_store_pg` fixture 이름은 `test_llm_rag_pgvector.py` 상단을 열어 기존에 쓰이는 것과 동일한 이름으로 맞춘다 — 이 파일에 이미 pgvector 테스트가 있으므로 그 fixture를 그대로 재사용한다.)

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_rag_pgvector.py -k consistency -v`
Expected: 로컬에 Postgres(pgvector)가 있으면 FAIL(`ImportError: cannot import name 'check_voice_consistency'`), 없으면 SKIP — 어느 쪽이든 정상.

- [ ] **Step 4: rag.py에 함수 추가**

```python
_CONSISTENCY_MIN_BASELINE = 3
_CONSISTENCY_DISTANCE_THRESHOLD = 0.5


def check_voice_consistency(db: Session, store_id: int, category: str, candidate_embedding: list[float]) -> bool | None:
    """경로 C(배민 직접 답글) 후보가 이미 신뢰할 수 있는 예시(organic/
    backfill) 클러스터와 말투가 일관되는지 본다(스펙 1.4.1절) — "AI가
    썼는지"가 아니라 "이 가게 말투에 맞는지"를 묻는 질문으로 바꾼 것.
    비교할 기준(organic/backfill, embedding 있는 것)이
    _CONSISTENCY_MIN_BASELINE개 미만이면 판단 불가로 None을 반환한다
    (신생 매장은 이 체크를 건너뛴다)."""
    baseline_q = select(GoldenExample.id).where(
        GoldenExample.store_id == store_id,
        GoldenExample.category == category,
        GoldenExample.source.in_(("organic", "backfill")),
        GoldenExample.embedding.is_not(None),
    )
    baseline_count = len(db.scalars(baseline_q).all())
    if baseline_count < _CONSISTENCY_MIN_BASELINE:
        return None

    nearest = db.scalars(
        select(GoldenExample.embedding.cosine_distance(candidate_embedding))
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.source.in_(("organic", "backfill")),
            GoldenExample.embedding.is_not(None),
        )
        .order_by(GoldenExample.embedding.cosine_distance(candidate_embedding))
        .limit(3)
    ).all()
    avg_distance = sum(nearest) / len(nearest)
    return avg_distance <= _CONSISTENCY_DISTANCE_THRESHOLD


def promote_direct_reply_to_golden_example(db: Session, review: Review, reply_id: int, reply_text: str) -> GoldenExample:
    """review_sync.py가 extract_owner_reply로 감지한, 배민에 이미 달려있던
    사장님 답글을 golden_example로 승격한다(경로 C, 스펙 1.3/1.4.1절).
    이상치로 판정돼도 저장 자체는 막지 않는다 — needs_confirmation만
    세워서 나중에(Plan 4) 사장님 확인 UI가 쓸 수 있게 한다."""
    embedding = compute_golden_example_embedding(review.content)
    needs_confirmation = False
    if embedding is not None:
        consistent = check_voice_consistency(db, review.store_id, review.category, embedding)
        needs_confirmation = consistent is False

    example = GoldenExample(
        store_id=review.store_id, category=review.category,
        review_text=review.content, reply_text=reply_text,
        is_manual=True, is_synthetic=False, source="organic_direct",
        source_review_id=review.id, source_reply_id=reply_id,
        embedding=embedding, needs_confirmation=needs_confirmation,
        created_at=datetime.now(timezone.utc),
    )
    db.add(example)
    return example
```

(`rag.py` 상단 import에 `datetime, timezone`는 이미 있다. `GoldenExample`도 이미 import돼 있다.)

- [ ] **Step 5: review_sync.py에서 호출**

`owner_reply = extract_owner_reply(raw)` 블록(기존 459~465행대) 수정:

```python
                owner_reply = extract_owner_reply(raw)
                if owner_reply is not None:
                    reply_content, replied_at = owner_reply
                    reply_row = ReviewReply(
                        review_id=review.id, reply_type="final", style_id=None,
                        content=reply_content, created_at=replied_at,
                    )
                    db.add(reply_row)
                    db.flush()
                    promote_direct_reply_to_golden_example(db, review, reply_row.id, reply_content)
```

import 구간에 `from app.llm.rag import promote_direct_reply_to_golden_example` 추가(기존에 `count_recent_same_category`, `fetch_golden_examples` 등을 이미 `app.llm.rag`에서 import하고 있다면 같은 줄에 합친다).

- [ ] **Step 6: review_sync.py 승격 배선 테스트**

```python
def test_sync_promotes_existing_owner_reply_to_golden_example(db_session, seeded_user, platforms, monkeypatch):
    # 기존 test_review_sync.py의 "이미 배민에 사장님 답글이 달려있는 리뷰"
    # 테스트(extract_owner_reply 관련)와 같은 셋업을 재사용한다 — 그
    # 테스트가 raw 응답에 comments 배열을 채워 fetch_all_reviews를
    # monkeypatch하는 방식을 그대로 따라 쓴다.
    ...  # 기존 파일의 동일 패턴 셋업 함수/monkeypatch를 그대로 가져다 씀

    from app.models import GoldenExample

    examples = db_session.query(GoldenExample).filter_by(source="organic_direct").all()
    assert len(examples) == 1
    assert examples[0].reply_text  # 실제 댓글 내용과 동일
```

(기존 `test_review_sync.py`에 이미 `extract_owner_reply` 경로를 트리거하는 테스트가 있을 가능성이 높다 — 그 테스트 함수를 먼저 찾아 읽고, 그 셋업 패턴을 그대로 복사해 이 assert만 추가한다. "Similar to Task N"이 아니라 실제로 그 파일을 열어 정확한 monkeypatch 대상/셋업 코드를 그대로 옮겨 적을 것.)

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_review_sync.py -k "owner_reply or golden_example" tests/test_llm_rag_pgvector.py -v`
Expected: PASS(pgvector 테스트는 로컬 Postgres 있으면 PASS, 없으면 SKIP)

- [ ] **Step 8: Commit**

```bash
git add schema.sql backend/app/models.py backend/app/llm/rag.py backend/app/review_sync.py backend/tests/test_llm_rag_pgvector.py backend/tests/test_review_sync.py
git commit -m "feat: 배민 직접 답글(경로 C) golden_example 승격 + 임베딩 일관성 체크"
```

---

### Task 8: fetch_golden_examples를 3단계(source 기반) 우선순위로 재작성 + synthetic 제거

**Files:**
- Modify: `schema.sql` (golden_examples CHECK, 인덱스)
- Modify: `backend/app/llm/rag.py`
- Modify: `backend/tests/test_llm_rag.py`
- Delete: `backend/scripts/seed_synthetic_golden_examples.py`
- Delete: `backend/tests/test_seed_synthetic_golden_examples.py`

**Interfaces:**
- Consumes: `GoldenExample.source`, `Review`(JOIN 대상, 신호 계산용).
- Produces: `fetch_golden_examples(db, store_id, category, query_text, limit=3) -> list[GoldenExample]` — 시그니처는 그대로, 내부 우선순위 로직만 교체.

- [ ] **Step 1: schema.sql 정리**

Task 7에서 이미 CHECK 제약에서 `'synthetic'`을 뺐는지 확인(안 뺐으면 지금 뺀다). 인덱스도 `source` 포함하도록 교체:

```sql
DROP INDEX IF EXISTS idx_golden_examples_lookup;
CREATE INDEX idx_golden_examples_lookup
    ON golden_examples(store_id, category, source, created_at DESC);
```

(이 `DROP INDEX`/`CREATE INDEX` 쌍은 schema.sql이 처음부터 다시 실행되는 걸 전제로 한 정본 문서이므로 — 운영 DB 마이그레이션은 이 플랜 범위 밖의 별도 배포 작업이라고 CLAUDE.md 관례상 전제한다.)

- [ ] **Step 2: 기존 synthetic 관련 테스트 삭제/교체**

`backend/tests/test_llm_rag.py`의 `test_fetch_golden_examples_prefers_real_over_synthetic`과 `test_fetch_golden_examples_backfills_with_synthetic_when_real_insufficient`를 삭제하고, `_make_example` 헬퍼를 `source` 파라미터를 받도록 수정한 뒤 새 3단계 테스트로 교체:

```python
from datetime import datetime, timedelta, timezone

from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, Review


def _make_example(db_session, store_id, *, category, source, created_at):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text="리뷰", reply_text="답글",
        is_manual=True, is_synthetic=False, source=source,
        created_at=created_at,
    )
    db_session.add(ex)
    return ex


def _make_signal_review(db_session, store_id, platform_id, *, category, sentiment_conflict=False, created_at):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="치킨",
        rating=1, content="리뷰 본문", customer_nickname="손님",
        category=category, sentiment_conflict=sentiment_conflict, created_at=created_at,
    )
    db_session.add(review)
    db_session.flush()
    return review


def test_fetch_golden_examples_prefers_high_confidence_organic(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    # 고신뢰: 불만 카테고리(delivery != no_issue)인 리뷰에 연결된 organic
    signal_review = _make_signal_review(db_session, sid, pid, category="delivery", created_at=now)
    high_conf = _make_example(db_session, sid, category="delivery", source="organic", created_at=now)
    high_conf.source_review_id = signal_review.id
    # 일반: 신호 없는 organic (연결된 리뷰 없음)
    _make_example(db_session, sid, category="delivery", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=1)

    assert result[0].id == high_conf.id


def test_fetch_golden_examples_falls_back_to_onboarding_when_organic_insufficient(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    organic = _make_example(db_session, sid, category="hygiene", source="organic", created_at=now)
    onboarding = _make_example(db_session, sid, category="hygiene", source="onboarding", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=2)

    assert len(result) == 2
    assert result[0].id == organic.id
    assert result[1].id == onboarding.id


def test_fetch_golden_examples_filters_by_category(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", source="organic", created_at=now)
    _make_example(db_session, sid, category="delivery", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=3)

    assert len(result) == 1
    assert result[0].category == "delivery"


def test_fetch_golden_examples_falls_back_to_recency_when_embedding_unavailable(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    older = _make_example(db_session, sid, category="food_quality", source="organic", created_at=now - timedelta(days=5))
    newer = _make_example(db_session, sid, category="food_quality", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "food_quality", "쿼리", limit=2)

    assert result[0].id == newer.id
    assert result[1].id == older.id


def test_count_recent_same_category_within_window(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="배달 늦어요",
        customer_nickname="손님", category="delivery", created_at=now - timedelta(days=5),
    ))
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="또 배달 늦어요",
        customer_nickname="손님2", category="delivery", created_at=now - timedelta(days=40),
    ))
    db_session.commit()

    count = count_recent_same_category(db_session, sid, "delivery", days=30)

    assert count == 1
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_rag.py -v`
Expected: FAIL — `test_fetch_golden_examples_prefers_high_confidence_organic`가 신호 기반 우선순위를 아직 구현 안 해서 실패(기존 `_query_ranked`가 `is_manual`/`is_synthetic`만 보고 `source`나 신호를 전혀 안 봄)

- [ ] **Step 4: rag.py의 _query_ranked/fetch_golden_examples 재작성**

```python
def _has_confidence_signal_subquery():
    """1.4절 신호 3개 중 하나라도 있으면 True — review_count_at_time,
    불만 카테고리, sentiment_conflict. JOIN 대상 reviews가 없는(연결 안
    된) golden_example은 신호 없음으로 취급한다(LEFT JOIN + 신호 컬럼이
    NULL이면 False)."""
    review_count_subq = (
        select(func.count())
        .select_from(Review)
        .where(
            Review.store_id == GoldenExample.store_id,
            Review.created_at <= ReviewAlias.created_at,
        )
        .correlate(GoldenExample)
        .scalar_subquery()
    )
    # 위 서브쿼리는 아래 _query_ranked에서 reviews를 ReviewAlias로 alias한
    # 뒤 조합한다 — 이 헬퍼 함수 자체는 쓰지 않고, _query_ranked 안에 인라인으로
    # 풀어 쓴다(아래 Step 참고). 이 헬퍼는 설계 메모로만 남긴다.


from sqlalchemy.orm import aliased


def _query_ranked(
    db: Session, store_id: int, category: str, query_embedding: list[float] | None,
    limit: int, *, sources: tuple[str, ...], require_signal: bool | None = None,
) -> list[GoldenExample]:
    q = select(GoldenExample).where(
        GoldenExample.store_id == store_id,
        GoldenExample.category == category,
        GoldenExample.source.in_(sources),
    )

    if require_signal is not None:
        r = aliased(Review)
        older_count = (
            select(func.count())
            .select_from(Review)
            .where(Review.store_id == store_id, Review.created_at <= r.created_at)
            .correlate(r)
            .scalar_subquery()
        )
        has_signal = (
            select(func.count())
            .select_from(r)
            .where(
                r.id == GoldenExample.source_review_id,
                (older_count <= 3) | (r.category != "no_issue") | (r.sentiment_conflict.is_(True)),
            )
            .correlate(GoldenExample)
            .scalar_subquery()
        ) > 0
        q = q.where(has_signal.is_(require_signal))

    if query_embedding is not None:
        q = q.order_by(
            GoldenExample.embedding.is_(None),
            GoldenExample.embedding.cosine_distance(query_embedding),
            GoldenExample.created_at.desc(),
        )
    else:
        q = q.order_by(GoldenExample.created_at.desc())

    return list(db.scalars(q.limit(limit)).all())


def fetch_golden_examples(db: Session, store_id: int, category: str, query_text: str, limit: int = 3) -> list[GoldenExample]:
    """골든 예시 조회, 3단계 우선순위(스펙 1.4절): 고신뢰 organic(리뷰수≤3
    또는 불만카테고리 또는 sentiment_conflict 신호 보유) → 일반 organic
    (신호 없음) → onboarding. organic은 source in (organic, organic_direct,
    backfill) 전부를 묶는다 — 셋 다 사람이 직접 쓴 것으로 간주되는 소스고,
    일반/고신뢰 구분은 1.4절 신호로만 가른다."""
    try:
        query_embedding = embed_query(query_text)
    except Exception:
        query_embedding = None

    organic_sources = ("organic", "organic_direct", "backfill")

    high_confidence = _query_ranked(
        db, store_id, category, query_embedding, limit,
        sources=organic_sources, require_signal=True,
    )
    if len(high_confidence) >= limit:
        return high_confidence

    low_confidence = _query_ranked(
        db, store_id, category, query_embedding, limit - len(high_confidence),
        sources=organic_sources, require_signal=False,
    )
    combined = high_confidence + low_confidence
    if len(combined) >= limit:
        return combined

    onboarding = _query_ranked(
        db, store_id, category, query_embedding, limit - len(combined),
        sources=("onboarding",),
    )
    return combined + onboarding
```

(`rag.py` 상단 import에 `from sqlalchemy.orm import aliased`와 `from app.models import GoldenExample, Review` 추가 — `Review`는 이미 import돼 있다. 설계 메모로만 남긴 `_has_confidence_signal_subquery` 더미 함수는 실제로 파일에 넣지 않는다 — 삭제하고 `_query_ranked` 안의 인라인 버전만 쓴다.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_rag.py -v`
Expected: PASS 전체

- [ ] **Step 6: synthetic 스크립트/테스트 삭제**

```bash
rm backend/scripts/seed_synthetic_golden_examples.py
rm backend/tests/test_seed_synthetic_golden_examples.py
```

- [ ] **Step 7: 전체 테스트 스위트 확인**

Run: `cd backend && python -m pytest -v`
Expected: PASS 전체(`seed_synthetic_golden_examples` 관련 테스트는 더 이상 수집 안 됨을 확인)

- [ ] **Step 8: Commit**

```bash
git add -A backend/scripts backend/tests schema.sql backend/app/llm/rag.py
git commit -m "refactor: fetch_golden_examples를 3단계 source 기반 우선순위로 재작성, synthetic 메커니즘 제거"
```

---

## Self-Review

**스펙 커버리지**: 1.1(절차기억 테이블+이관) Task1-2, delivery 경계 규칙 Task1-3, 1.2(사장님공지) Task4-6, 1.3(경로 A/B/C) — A/B는 기존 코드 그대로, C는 Task7, 1.4(3단계 우선순위) Task8, 1.4.1(임베딩 일관성) Task7. 전부 커버됨.

**플레이스홀더 스캔**: Task6/Task7에 "기존 테스트 패턴을 그대로 복사"라고 적은 두 군데(Task6 Step3 일부 서술, Task7 Step6)는 `test_review_sync.py`가 130KB라 정확한 라인을 지금 다 옮겨 적으면 플랜 자체가 과도하게 길어져서 남긴 의도적 지시다 — 구현자가 그 한 파일만 열어 보면 바로 찾을 수 있는 셋업이라 "Similar to Task N"(다른 태스크 참조) 금지 규칙과는 다른 경우로 판단했다. 나머지는 전부 실제 코드.

**타입 일관성**: `fetch_golden_examples`의 시그니처(Task8)는 Task2/6에서 호출하는 쪽과 동일(`db, store_id, category, query_text, limit`). `GoldenExample.source`는 Task7에서 CHECK에 `organic_direct` 추가, Task8에서 `synthetic` 제거 — 두 수정이 같은 CHECK 절을 건드리므로 Task7을 Task8보다 먼저 실행해야 한다(순서 그대로 둠, 플랜 번호 순서가 곧 의존 순서).
