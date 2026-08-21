# LLM + RAG 답글 온보딩 데이터 부트스트랩 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `golden_examples`가 비어있는 카테고리마다 가상 리뷰 + 마중물 초안을 사장님에게 보여주고, 직접 고쳐 쓴 답변을 진짜 골든 예시로 승격시키는 온보딩 부트스트랩 플로우(빠른 마법사 + 하루 단위 트리클)를 구축한다. 추가로 가게와 무관한 범용 시드 골든 예시를 매장마다 한 번씩 심어 신규 가게도 첫날부터 어느 정도 참고할 예시를 갖게 한다.

**Architecture:** 새 `onboarding_scenarios` 테이블(매장×카테고리당 1행, UNIQUE 제약)이 마법사와 트리클이 공유하는 상태를 담는다. `backend/app/llm/onboarding.py`가 커버리지 스캔(`find_uncovered_categories`) + 시나리오 조회/생성(`get_or_create_scenario`)을 담당하고, 마중물 초안 생성은 기존 `generate_ai_reply`를 DB에 저장하지 않는 임시 `Review` 객체로 그대로 재사용한다. `backend/app/routers/reply_onboarding.py`가 4개 엔드포인트(마법사/오늘의 트리클/답변/건너뛰기)를 제공하고, 답변 시 기존 `refresh_store_style_profile_background`를 그대로 재사용해 스타일 프로파일을 갱신한다. 프론트엔드는 대시보드에 "오늘의 답글 훈련" 카드, 가게 연결 화면에 배민 로그인 성공 직후 뜨는 마법사 모달을 추가한다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql 정본, SQLite로 테스트), Anthropic API(Haiku/Sonnet, `app.llm.client` 경유), Next.js/React/TypeScript.

## Global Constraints

- 벡터 검색은 도입하지 않는다 — 카테고리 필터 + 최신순 LIMIT만 쓰는 기존 `app.llm.rag` 검색 방식을 그대로 따른다.
- `brands` 테이블(원산지·메뉴 등 사실 정보), 브랜드별(`brand_id`) 말투 분리는 이번 범위가 아니다 — 손대지 않는다.
- AI가 진짜 데이터를 복제·증강해서 새 학습 데이터를 만드는 방식은 절대 쓰지 않는다 — 데이터를 늘리는 유일한 경로는 사장님에게서 새 진짜 답변을 받는 것(온보딩)과, 가게와 무관함을 명시한 범용 시드(`is_synthetic=true`)뿐이다.
- 가상 리뷰(`fake_review`)는 `reviews` 테이블에 저장하지 않는다(`db.add()` 호출 금지) — 실제 리뷰 데이터를 가상 데이터로 오염시키면 안 된다.
- 온보딩으로 만든 골든 예시(`source="onboarding"`)는 스타일 프로파일 재생성(`refresh_store_style_profile`)의 `is_manual=true, is_synthetic=false` 필터에 그대로 포함되어야 한다 — 새 필터링 로직을 만들지 않는다.
- 모든 라우터에서 Anthropic 클라이언트 호출 모듈은 `from app.llm import client` 형태(모듈째 임포트)로 가져오고 `client.call_haiku(...)`/`client.call_sonnet(...)`로 호출한다 — `from app.llm.client import call_haiku` 같은 개별 이름 임포트는 테스트의 `monkeypatch.setattr(<module>.client, "call_haiku", ...)`가 전파되지 않아 금지.
- FastAPI `BackgroundTasks`에는 요청 스코프 `db: Session`을 절대 넘기지 않는다 — 백그라운드 작업은 항상 자체 `SessionLocal()`을 여는 래퍼(`refresh_store_style_profile_background` 재사용)를 거친다.
- `store_id` 쿼리 파라미터를 명시적으로 받는 엔드포인트는 기존 `sales.py`/`reply_settings.py` 관례를 따라 소유권을 별도로 검증하지 않는다(`sid = store_id or get_user_default_store_id(user, db)`만 수행) — 이 프로젝트는 사장 1명 = 매장 1개 기준이라 기존 라우터 전체가 이 패턴이다. 단, path param으로 받는 리소스 ID(`scenario_id`)는 `reviews.py`/`store_connections.py` 관례대로 소유자 확인 후 404 처리한다.

---

### Task 1: `onboarding_scenarios` 테이블 + `OnboardingScenario` 모델

**Files:**
- Modify: `schema.sql` (COMMIT 직전에 테이블 23 추가)
- Modify: `backend/app/models.py` (파일 끝, `SignupVerification` 클래스 뒤에 추가)
- Test: `backend/tests/test_llm_models.py` (기존 파일에 테스트 추가)

**Interfaces:**
- Consumes: 없음 (신규 테이블)
- Produces: `OnboardingScenario` 모델 — `id, store_id, category, virtual_review_text, draft_text, status, shown_on, created_at` 필드 + `store: Mapped[Store]` 관계. Task 2(`onboarding.py`)와 Task 4(라우터)가 이 모델을 `from app.models import OnboardingScenario`로 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_models.py` 파일 끝에 아래를 추가한다 (기존 임포트 줄 `from app.models import GoldenExample, Review, StoreStyleProfile`을 `from app.models import GoldenExample, OnboardingScenario, Review, StoreStyleProfile`로 바꾸고, 파일 맨 아래에 두 테스트를 추가):

```python
def test_onboarding_scenario_round_trips(db_session, seeded_user):
    scenario = OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="포장에서 냄새가 나요", draft_text="죄송합니다, 확인하겠습니다",
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    row = db_session.query(OnboardingScenario).filter_by(id=scenario.id).one()
    assert row.category == "hygiene"
    assert row.status == "pending"
    assert row.shown_on is None


def test_onboarding_scenario_unique_per_store_and_category(db_session, seeded_user):
    from sqlalchemy.exc import IntegrityError

    db_session.add(OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="첫 번째", draft_text="첫 번째 초안",
        status="pending", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    db_session.add(OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="두 번째", draft_text="두 번째 초안",
        status="pending", created_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

파일 맨 위 `from datetime import datetime, timezone` 아래에 `import pytest`를 추가한다(현재 파일에 없다면).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'OnboardingScenario' from 'app.models'`

- [ ] **Step 3: schema.sql에 테이블 추가**

`schema.sql`의 `-- 22. payments` 섹션과 `COMMIT;` 사이(파일 맨 끝, `COMMIT;` 바로 앞)에 추가:

```sql
-- ----------------------------------------------------------------------------
-- 23. onboarding_scenarios — 카테고리별 golden_examples가 비어있을 때 사장님께
--     보여줄 가상 리뷰 + 마중물 초안. 마법사(배민 연결 직후)와 트리클(대시보드
--     하루 단위)이 이 테이블을 공유한다. UNIQUE로 매장×카테고리당 1행만
--     존재하게 강제해 재사용 원칙을 스키마 레벨에서도 지킨다.
-- ----------------------------------------------------------------------------
CREATE TABLE onboarding_scenarios (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category            VARCHAR(24)  NOT NULL,
    virtual_review_text TEXT         NOT NULL,
    draft_text          TEXT         NOT NULL,
    status              VARCHAR(10)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'answered', 'skipped')),
    shown_on            DATE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (store_id, category)
);
```

- [ ] **Step 4: models.py에 모델 추가**

`backend/app/models.py` 맨 끝(`SignupVerification` 클래스 뒤)에 추가:

```python


class OnboardingScenario(Base):
    __tablename__ = "onboarding_scenarios"
    __table_args__ = (UniqueConstraint("store_id", "category"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    virtual_review_text: Mapped[str] = mapped_column(Text)
    draft_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    shown_on: Mapped[date | None]
    created_at: Mapped[datetime]

    store: Mapped[Store] = relationship()
```

`date`는 이미 파일 맨 위에서 `from datetime import date, datetime`으로 임포트돼 있다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_models.py -v`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_llm_models.py
git commit -m "feat: onboarding_scenarios 테이블 + 모델 추가"
```

---

### Task 2: 온보딩 핵심 로직 (`backend/app/llm/onboarding.py`)

**Files:**
- Modify: `backend/app/llm/generate.py` (private `_CATEGORY_LABELS`를 public `CATEGORY_LABELS`로 rename — 두 모듈이 공유하므로)
- Create: `backend/app/llm/onboarding.py`
- Test: `backend/tests/test_llm_onboarding.py`

**Interfaces:**
- Consumes: `app.llm.client.call_haiku(system, user, *, max_tokens=300) -> str`, `app.llm.generate.generate_ai_reply(db, review, store) -> str`, `app.llm.generate.CATEGORY_LABELS: dict[str, str]`, `app.llm.classify.VALID_CATEGORIES: tuple[str, ...]`, `app.models.GoldenExample`, `app.models.OnboardingScenario`(Task 1), `app.models.Review`, `app.models.Store`.
- Produces: `find_uncovered_categories(db, store_id) -> list[str]`, `get_or_create_scenario(db, store, category) -> OnboardingScenario`, `generate_virtual_review(category) -> str` — Task 4(라우터)가 앞의 두 함수를 가져다 쓴다.

- [ ] **Step 1: generate.py의 `_CATEGORY_LABELS`를 `CATEGORY_LABELS`로 rename**

`backend/app/llm/generate.py`에서 `_CATEGORY_LABELS = {` 를 `CATEGORY_LABELS = {`로, 그 아래 `category_label = _CATEGORY_LABELS.get(review.category, review.category)` 를 `category_label = CATEGORY_LABELS.get(review.category, review.category)`로 바꾼다. (이 값은 온보딩에서도 그대로 재사용해야 하는데, 언더스코어 접두 이름을 다른 모듈에서 가져다 쓰는 건 어색하므로 공개 이름으로 승격한다.) `test_llm_generate.py`는 이 상수를 직접 참조하지 않으므로 영향 없다.

Run: `cd backend && python -m pytest tests/test_llm_generate.py -v` — rename 직후에도 PASS인지 확인.

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_llm_onboarding.py` 신규 생성:

```python
from datetime import datetime, timezone

from app.llm import generate, onboarding
from app.models import GoldenExample, OnboardingScenario, Review


def test_find_uncovered_categories_excludes_covered_and_no_issue(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="옛날 리뷰", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    result = onboarding.find_uncovered_categories(db_session, sid)

    assert "hygiene" not in result
    assert "no_issue" not in result
    assert set(result) == {"food_quality", "delivery", "service", "price", "missing_or_wrong_item"}


def test_find_uncovered_categories_ignores_synthetic_examples(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="범용 예시", reply_text="범용 답글",
        is_manual=False, is_synthetic=True, source="synthetic", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    result = onboarding.find_uncovered_categories(db_session, sid)

    assert "hygiene" in result  # synthetic 시드만으로는 커버된 것으로 치지 않는다


def test_find_uncovered_categories_all_six_for_fresh_store(db_session, seeded_user):
    result = onboarding.find_uncovered_categories(db_session, seeded_user["store"].id)
    assert set(result) == {
        "food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item",
    }


def test_get_or_create_scenario_creates_new_one(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: "가상 리뷰 본문")
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "마중물 초안")

    scenario = onboarding.get_or_create_scenario(db_session, store, "hygiene")

    assert scenario.category == "hygiene"
    assert scenario.virtual_review_text == "가상 리뷰 본문"
    assert scenario.draft_text == "마중물 초안"
    assert scenario.status == "pending"
    assert db_session.query(Review).count() == 0  # 가상 리뷰는 reviews 테이블에 저장되지 않는다


def test_get_or_create_scenario_reuses_existing_without_calling_llm_again(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    calls = []
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: calls.append(1) or "가상 리뷰")
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "초안")

    first = onboarding.get_or_create_scenario(db_session, store, "hygiene")
    second = onboarding.get_or_create_scenario(db_session, store, "hygiene")

    assert first.id == second.id
    assert len(calls) == 1


def test_get_or_create_scenario_reuses_skipped_scenario_without_regenerating(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    db_session.add(OnboardingScenario(
        store_id=store.id, category="hygiene", virtual_review_text="가상 리뷰", draft_text="초안",
        status="skipped", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    def _boom(*a, **kw):
        raise AssertionError("이미 시나리오가 있으면 LLM을 다시 호출하면 안 된다")
    monkeypatch.setattr(onboarding.client, "call_haiku", _boom)

    scenario = onboarding.get_or_create_scenario(db_session, store, "hygiene")
    assert scenario.status == "skipped"


def test_generate_virtual_review_uses_category_label(monkeypatch):
    captured = {}

    def _fake_call_haiku(system, user, **kw):
        captured["user"] = user
        return "가상 리뷰"

    monkeypatch.setattr(onboarding.client, "call_haiku", _fake_call_haiku)

    result = onboarding.generate_virtual_review("hygiene")

    assert result == "가상 리뷰"
    assert "위생" in captured["user"]
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_llm_onboarding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.onboarding'`

- [ ] **Step 4: `backend/app/llm/onboarding.py` 구현**

```python
"""온보딩 데이터 부트스트랩 — golden_examples가 비어있는 카테고리마다 가상
리뷰 + 마중물 초안을 만들어 사장님에게 보여주고, 사장님이 직접 고친 답변을
진짜 golden_example로 승격시키는 흐름의 핵심 로직. AI가 진짜 데이터를
복제·증강하는 게 아니라, 사장님에게서 새 진짜 데이터를 능동적으로 받는
방식으로만 데이터를 늘린다(모델 붕괴 방지 원칙, 설계 문서
2026-08-21-llm-rag-reply-onboarding-design.md 참고)."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.classify import VALID_CATEGORIES
from app.llm.generate import CATEGORY_LABELS, generate_ai_reply
from app.models import GoldenExample, OnboardingScenario, Review, Store

# 실제 별점이 아니라 프롬프트 컨텍스트용 플레이스홀더 — generate_ai_reply의
# 프롬프트가 별점을 참고하므로 대표값만 채운다. 정밀할 필요 없다.
_REPRESENTATIVE_RATING = {
    "food_quality": 2,
    "delivery": 1,
    "hygiene": 1,
    "service": 2,
    "missing_or_wrong_item": 1,
    "price": 3,
}

_VIRTUAL_REVIEW_PROMPT_TEMPLATE = """너는 배달 음식점에 실제로 달릴 법한
고객 불만 리뷰를 하나 만든다. 아래 불만 유형에 해당하는, 자연스러운
한국어 리뷰를 1~3문장으로 작성하라. 특정 가게 이름이나 메뉴는 언급하지
말고, 일반적인 상황으로 써라.

불만 유형: {category_label}

리뷰 본문만 출력하고 다른 설명은 붙이지 마라."""


def generate_virtual_review(category: str) -> str:
    label = CATEGORY_LABELS[category]
    return client.call_haiku(
        "너는 배달앱 리뷰 예시를 만드는 도구다.",
        _VIRTUAL_REVIEW_PROMPT_TEMPLATE.format(category_label=label),
        max_tokens=200,
    )


def find_uncovered_categories(db: Session, store_id: int) -> list[str]:
    covered = set(db.scalars(
        select(GoldenExample.category).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        ).distinct()
    ).all())
    return [c for c in VALID_CATEGORIES if c != "no_issue" and c not in covered]


def get_or_create_scenario(db: Session, store: Store, category: str) -> OnboardingScenario:
    """카테고리 하나에 대해 기존 시나리오가 있으면(pending/skipped 상관없이)
    재사용하고, 없으면 새로 만든다. 호출자가 이미 find_uncovered_categories로
    real golden_example이 있는 카테고리를 걸러낸 뒤 부르므로 여기서는
    고려하지 않는다."""
    existing = db.scalar(
        select(OnboardingScenario).where(
            OnboardingScenario.store_id == store.id,
            OnboardingScenario.category == category,
        )
    )
    if existing is not None:
        return existing

    virtual_review_text = generate_virtual_review(category)
    # DB에 저장하지 않는 임시 Review — 진짜 reviews 테이블을 가상 데이터로
    # 오염시키면 안 된다. generate_ai_reply는 이 속성들만 읽는다(review.id는
    # 참조하지 않음).
    fake_review = Review(
        store_id=store.id, platform_id=0, menu_summary="",
        rating=_REPRESENTATIVE_RATING[category], content=virtual_review_text,
        customer_nickname="", customer_order_count=1, category=category,
        is_sensitive=(category == "hygiene"), created_at=datetime.now(timezone.utc),
    )
    draft_text = generate_ai_reply(db, fake_review, store)

    scenario = OnboardingScenario(
        store_id=store.id, category=category,
        virtual_review_text=virtual_review_text, draft_text=draft_text,
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db.add(scenario)
    db.commit()
    return scenario
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_llm_onboarding.py tests/test_llm_generate.py -v`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/llm/generate.py backend/app/llm/onboarding.py backend/tests/test_llm_onboarding.py
git commit -m "feat: 온보딩 커버리지 스캔 + 시나리오 생성 로직 추가"
```

---

### Task 3: 범용 시드 골든 예시 스크립트

**Files:**
- Create: `backend/scripts/seed_synthetic_golden_examples.py`
- Test: `backend/tests/test_seed_synthetic_golden_examples.py`

**Interfaces:**
- Consumes: `app.models.GoldenExample`, `app.models.Store`, `app.db.SessionLocal`.
- Produces: `seed_synthetic_golden_examples(db: Session) -> int` — 독립 실행 스크립트라 다른 태스크가 임포트하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_seed_synthetic_golden_examples.py` 신규 생성:

```python
from app.models import GoldenExample
from scripts.seed_synthetic_golden_examples import _SEED_EXAMPLES, seed_synthetic_golden_examples

_ALL_CATEGORIES = {"food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item"}


def test_seed_creates_one_example_per_category_per_store(db_session, seeded_user):
    count = seed_synthetic_golden_examples(db_session)
    assert count == 6

    rows = db_session.query(GoldenExample).filter_by(
        store_id=seeded_user["store"].id, is_synthetic=True,
    ).all()
    assert {r.category for r in rows} == _ALL_CATEGORIES
    assert all(r.is_manual is False for r in rows)
    assert all(r.source == "synthetic" for r in rows)
    assert all(r.review_text and r.reply_text for r in rows)


def test_seed_is_idempotent(db_session, seeded_user):
    seed_synthetic_golden_examples(db_session)
    second_count = seed_synthetic_golden_examples(db_session)
    assert second_count == 0
    assert db_session.query(GoldenExample).filter_by(
        store_id=seeded_user["store"].id, is_synthetic=True,
    ).count() == 6


def test_seed_covers_all_valid_categories():
    assert set(_SEED_EXAMPLES.keys()) == _ALL_CATEGORIES
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_seed_synthetic_golden_examples.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.seed_synthetic_golden_examples'`

- [ ] **Step 3: 스크립트 구현**

`backend/scripts/seed_synthetic_golden_examples.py` 신규 생성:

```python
"""가게와 무관한 범용 "모범 답안" 시드 — 신규 가게가 아직 진짜
golden_example이 없는 카테고리에서도 즉시 어느 정도 참고할 예시를 갖도록
하는 콜드스타트 폴백이다. is_synthetic=true라 store_style_profile 추출
(refresh_store_style_profile)과 온보딩 커버리지 스캔
(find_uncovered_categories) 양쪽에서 전부 제외되고, fetch_golden_examples가
real 예시 부족분을 채울 때만 참고된다(설계 문서
2026-08-21-llm-rag-reply-onboarding-design.md 참고). 매장마다 카테고리당
1건씩만 있으면 되므로, 이미 있으면 건너뛴다(멱등) — 신규 매장이 생길
때마다 다시 실행해도 안전하다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import GoldenExample, Store

_SEED_EXAMPLES: dict[str, tuple[str, str]] = {
    "food_quality": (
        "닭이 너무 퍽퍽하고 식어서 왔어요. 맛이 예전같지 않네요.",
        "안녕하세요, 소중한 리뷰 남겨주셔서 감사합니다. 말씀해주신 맛과 관련된 부분, "
        "조리 과정을 다시 한번 꼼꼼히 점검하겠습니다. 기대하신 만큼 만족을 드리지 못해 "
        "죄송한 마음입니다. 다음에는 더 신경 써서 준비하겠습니다.",
    ),
    "delivery": (
        "주문한 지 1시간 넘게 걸려서 왔어요. 배달이 너무 늦습니다.",
        "안녕하세요, 배달 관련해서 불편을 드려 죄송합니다. 도착 시간과 포장 상태 모두 "
        "다시 한번 점검하고, 배달 파트너와도 상황을 공유하겠습니다. 소중한 시간 "
        "기다리시게 해드려 죄송하고, 앞으로 더 신경 쓰겠습니다.",
    ),
    "hygiene": (
        "포장에서 이상한 냄새가 나고 위생 상태가 걱정되네요.",
        "안녕하세요, 이런 불편을 드려 정말 죄송합니다. 말씀해주신 부분은 가볍게 넘기지 "
        "않고 바로 확인해서 원인을 찾아보겠습니다. 혹시 괜찮으시면 가게로 연락 한번 "
        "주시면 자세히 안내드리겠습니다. 다시 한번 죄송하고, 더 세심하게 신경 쓰겠습니다.",
    ),
    "service": (
        "전화로 문의했는데 응대가 너무 불친절했어요.",
        "안녕하세요, 응대 과정에서 불편을 드려 죄송합니다. 말씀해주신 내용 무겁게 "
        "받아들이고, 다시는 이런 일이 없도록 신경 쓰겠습니다. 소중한 의견 남겨주셔서 "
        "감사드리고, 더 나은 모습으로 찾아뵙겠습니다.",
    ),
    "price": (
        "양에 비해 가격이 좀 비싸다고 느껴져요.",
        "안녕하세요, 가격에 대해 아쉬운 마음 남겨주셔서 감사합니다. 저희도 재료와 "
        "품질을 유지하면서 최대한 합리적인 가격을 고민하고 있습니다. 말씀해주신 의견 "
        "참고해서 계속 더 나은 방법을 찾아보겠습니다.",
    ),
    "missing_or_wrong_item": (
        "주문한 메뉴가 아니라 다른 메뉴가 왔어요. 확인 좀 해주세요.",
        "안녕하세요, 주문하신 것과 다르게 받으셔서 많이 당황하셨겠습니다. 정말 "
        "죄송합니다. 포장 과정을 다시 한번 꼼꼼히 확인하도록 하겠습니다. 불편하신 "
        "부분 있으시면 가게로 연락 주시면 바로 도와드리겠습니다.",
    ),
}


def seed_synthetic_golden_examples(db: Session) -> int:
    inserted = 0
    store_ids = db.scalars(select(Store.id)).all()
    for store_id in store_ids:
        for category, (review_text, reply_text) in _SEED_EXAMPLES.items():
            already = db.scalar(
                select(GoldenExample).where(
                    GoldenExample.store_id == store_id,
                    GoldenExample.category == category,
                    GoldenExample.is_synthetic.is_(True),
                )
            )
            if already is not None:
                continue
            db.add(GoldenExample(
                store_id=store_id, category=category,
                review_text=review_text, reply_text=reply_text,
                is_manual=False, is_synthetic=True, source="synthetic",
                created_at=datetime.now(timezone.utc),
            ))
            inserted += 1
    db.commit()
    return inserted


if __name__ == "__main__":
    session = SessionLocal()
    try:
        count = seed_synthetic_golden_examples(session)
        print(f"{count}건의 범용 시드 예시를 추가했습니다.")
    finally:
        session.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_seed_synthetic_golden_examples.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add backend/scripts/seed_synthetic_golden_examples.py backend/tests/test_seed_synthetic_golden_examples.py
git commit -m "feat: 매장별 범용 시드 골든 예시 스크립트 추가"
```

---

### Task 4: 온보딩 API 라우터

**Files:**
- Create: `backend/app/routers/reply_onboarding.py`
- Modify: `backend/app/main.py` (라우터 등록)
- Test: `backend/tests/test_reply_onboarding.py`

**Interfaces:**
- Consumes: `app.llm.onboarding.find_uncovered_categories`, `app.llm.onboarding.get_or_create_scenario`(Task 2), `app.llm.style_profile.refresh_store_style_profile_background`, `app.auth.get_current_user`, `app.auth.get_user_default_store_id`, `app.models.OnboardingScenario`(Task 1), `app.models.GoldenExample`, `app.models.Store`.
- Produces: `POST /reply-onboarding/wizard`, `GET /reply-onboarding/today`, `POST /reply-onboarding/scenarios/{id}/answer`, `POST /reply-onboarding/scenarios/{id}/skip` — 각 엔드포인트는 `{"id", "category", "virtual_review_text", "draft_text", "status"}` 형태의 JSON(또는 그 배열)을 반환한다. Task 5(프론트엔드)가 이 응답 형태를 그대로 소비한다.

**설계 문서와 다르게 이 태스크에서 명시적으로 보강하는 부분**: 설계 문서는 `GET /today`가 "find_uncovered_categories 중 최대 3개를 골라"라고만 적었는데, `find_uncovered_categories`는 항상 `VALID_CATEGORIES` 순서(`food_quality, delivery, hygiene, ...`)로 반환하므로 그대로 앞에서 3개를 자르면 사장님이 스킵한 이후에도 매일 같은 앞쪽 3개 카테고리만 반복해서 보여주고 뒤쪽 카테고리는 영원히 트리클에 노출되지 않는 버그가 생긴다. 그래서 이 태스크는 "아직 시나리오가 한 번도 생성된 적 없는 카테고리"를 "이미 시나리오가 있는(스킵됐거나 아직 pending인) 카테고리"보다 항상 먼저 보여주도록 정렬한다 — 6개 카테고리를 전부 한 바퀴 돈 뒤에야 재노출이 시작된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_reply_onboarding.py` 신규 생성:

```python
from datetime import date, datetime, timedelta, timezone

from app.llm import generate, onboarding
from app.models import GoldenExample, OnboardingScenario

_ALL_CATEGORIES = {"food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item"}


def _patch_llm(monkeypatch, virtual_review="가상 리뷰 본문", draft_text="마중물 초안"):
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: virtual_review)
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: draft_text)


def test_wizard_returns_all_uncovered_categories(client, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    assert res.status_code == 200
    categories = {row["category"] for row in res.json()}
    assert categories == _ALL_CATEGORIES


def test_wizard_excludes_covered_categories(client, db_session, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    db_session.add(GoldenExample(
        store_id=seeded_user["store"].id, category="hygiene", review_text="옛날", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    categories = {row["category"] for row in res.json()}
    assert "hygiene" not in categories
    assert len(categories) == 5


def test_wizard_returns_empty_when_fully_covered(client, db_session, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    for c in _ALL_CATEGORIES:
        db_session.add(GoldenExample(
            store_id=seeded_user["store"].id, category=c, review_text="옛날", reply_text="옛날 답글",
            is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    assert res.json() == []


def test_today_limits_to_three_scenarios(client, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    res = client.get("/reply-onboarding/today", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json()) == 3


def test_today_is_stable_within_the_same_day(client, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    first = client.get("/reply-onboarding/today", headers=auth_headers).json()
    second = client.get("/reply-onboarding/today", headers=auth_headers).json()
    assert [row["id"] for row in first] == [row["id"] for row in second]


def test_today_excludes_already_answered_same_day_scenario(client, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    first = client.get("/reply-onboarding/today", headers=auth_headers).json()
    answered_id = first[0]["id"]
    client.post(f"/reply-onboarding/scenarios/{answered_id}/answer", json={"content": "실제 답글"}, headers=auth_headers)

    second = client.get("/reply-onboarding/today", headers=auth_headers).json()
    assert answered_id not in [row["id"] for row in second]
    assert len(second) == 2


def test_today_prioritizes_never_shown_categories_over_previously_shown(client, db_session, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    first_day = client.get("/reply-onboarding/today", headers=auth_headers).json()
    first_categories = {row["category"] for row in first_day}
    assert len(first_categories) == 3

    for row in first_day:
        client.post(f"/reply-onboarding/scenarios/{row['id']}/skip", headers=auth_headers)

    # "다음날"처럼 shown_on을 하루 전으로 되돌려 "오늘 아직 안 보여준" 상태를 재현한다.
    yesterday = date.today() - timedelta(days=1)
    for row in first_day:
        scenario = db_session.get(OnboardingScenario, row["id"])
        scenario.shown_on = yesterday
    db_session.commit()

    second_day = client.get("/reply-onboarding/today", headers=auth_headers).json()
    second_categories = {row["category"] for row in second_day}
    assert second_categories.isdisjoint(first_categories)
    assert first_categories | second_categories == _ALL_CATEGORIES


def test_answer_promotes_to_golden_example_and_triggers_style_refresh(client, db_session, seeded_user, auth_headers, monkeypatch):
    from app.routers import reply_onboarding as reply_onboarding_mod

    _patch_llm(monkeypatch)
    refreshed = []
    monkeypatch.setattr(reply_onboarding_mod, "refresh_store_style_profile_background", lambda store_id: refreshed.append(store_id))

    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")

    res = client.post(
        f"/reply-onboarding/scenarios/{target['id']}/answer",
        json={"content": "실제로 이렇게 답할게요"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "answered"

    example = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").one()
    assert example.reply_text == "실제로 이렇게 답할게요"
    assert example.review_text == target["virtual_review_text"]
    assert example.is_manual is True
    assert example.is_synthetic is False
    assert example.source == "onboarding"
    assert refreshed == [seeded_user["store"].id]


def test_answer_promotes_even_when_identical_to_draft(client, db_session, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch, draft_text="이대로 괜찮아요")
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")
    assert target["draft_text"] == "이대로 괜찮아요"

    client.post(
        f"/reply-onboarding/scenarios/{target['id']}/answer",
        json={"content": "이대로 괜찮아요"},
        headers=auth_headers,
    )

    # save_final_reply(코어 설계)와 달리 diff 비교 없이 항상 승격한다 — 온보딩은
    # 애초에 사장님이 검토·제출한 것이므로 초안과 같아도 진짜 데이터로 취급한다.
    count = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").count()
    assert count == 1


def test_answer_already_answered_returns_409(client, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = scenarios[0]
    client.post(f"/reply-onboarding/scenarios/{target['id']}/answer", json={"content": "답변"}, headers=auth_headers)

    res = client.post(f"/reply-onboarding/scenarios/{target['id']}/answer", json={"content": "또 답변"}, headers=auth_headers)
    assert res.status_code == 409


def test_skip_does_not_promote_and_stays_available_for_rescan(client, db_session, seeded_user, auth_headers, monkeypatch):
    _patch_llm(monkeypatch)
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")

    res = client.post(f"/reply-onboarding/scenarios/{target['id']}/skip", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"

    count = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").count()
    assert count == 0

    still_uncovered = onboarding.find_uncovered_categories(db_session, seeded_user["store"].id)
    assert "hygiene" in still_uncovered


def test_scenario_action_404_for_other_users_scenario(client, db_session, seeded_user, auth_headers, monkeypatch):
    from app.auth import hash_password
    from app.models import Store, User

    _patch_llm(monkeypatch)
    other_user = User(
        email="other@dris.kr", password_hash=hash_password("x"), nickname="다른사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()
    other_store = Store(user_id=other_user.id, name="다른가게", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.commit()

    other_scenario = onboarding.get_or_create_scenario(db_session, other_store, "hygiene")

    res = client.post(
        f"/reply-onboarding/scenarios/{other_scenario.id}/answer", json={"content": "몰래 답변"}, headers=auth_headers,
    )
    assert res.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_reply_onboarding.py -v`
Expected: FAIL — `404 Not Found` (라우터 미등록) 전반

- [ ] **Step 3: 라우터 구현**

`backend/app/routers/reply_onboarding.py` 신규 생성:

```python
"""온보딩 데이터 부트스트랩 API. 배민 실계정 연결 직후의 빠른 마법사와
대시보드의 하루 단위 트리클이 같은 onboarding_scenarios를 공유한다(설계
문서 2026-08-21-llm-rag-reply-onboarding-design.md 참고)."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.onboarding import find_uncovered_categories, get_or_create_scenario
from app.llm.style_profile import refresh_store_style_profile_background
from app.models import GoldenExample, OnboardingScenario, Store, User

router = APIRouter(tags=["reply-onboarding"])

_DAILY_TRICKLE_LIMIT = 3


def _row(s: OnboardingScenario) -> dict:
    return {
        "id": s.id,
        "category": s.category,
        "virtual_review_text": s.virtual_review_text,
        "draft_text": s.draft_text,
        "status": s.status,
    }


def _get_owned_scenario(db: Session, scenario_id: int, user: User) -> OnboardingScenario:
    scenario = db.get(OnboardingScenario, scenario_id, options=[joinedload(OnboardingScenario.store)])
    if scenario is None or scenario.store.user_id != user.id:
        raise HTTPException(404, "시나리오를 찾을 수 없습니다")
    return scenario


@router.post("/reply-onboarding/wizard")
def run_wizard(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None:
        raise HTTPException(404, "매장을 찾을 수 없습니다")

    categories = find_uncovered_categories(db, sid)
    scenarios = [get_or_create_scenario(db, store, c) for c in categories]
    return [_row(s) for s in scenarios]


@router.get("/reply-onboarding/today")
def get_today(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None:
        raise HTTPException(404, "매장을 찾을 수 없습니다")

    today = date.today()
    already_shown = db.scalars(
        select(OnboardingScenario).where(
            OnboardingScenario.store_id == sid,
            OnboardingScenario.shown_on == today,
        )
    ).all()
    if already_shown:
        return [_row(s) for s in already_shown if s.status == "pending"]

    uncovered = find_uncovered_categories(db, sid)
    existing_by_category = {
        s.category: s for s in db.scalars(
            select(OnboardingScenario).where(
                OnboardingScenario.store_id == sid,
                OnboardingScenario.category.in_(uncovered),
            )
        ).all()
    }
    # 아직 한 번도 시나리오가 만들어진 적 없는 카테고리를 먼저 보여준다 —
    # 그렇지 않으면 find_uncovered_categories가 항상 같은 순서로 반환하는
    # 카테고리 목록의 앞쪽 3개만 매일 반복 노출되고, 뒤쪽 카테고리는 사장님이
    # 앞쪽을 스킵해도 영원히 트리클에 나오지 않는다.
    never_shown = [c for c in uncovered if c not in existing_by_category]
    previously_shown = sorted(
        (c for c in uncovered if c in existing_by_category),
        key=lambda c: existing_by_category[c].created_at,
    )
    categories = (never_shown + previously_shown)[:_DAILY_TRICKLE_LIMIT]

    scenarios = []
    for c in categories:
        scenario = get_or_create_scenario(db, store, c)
        scenario.shown_on = today
        scenarios.append(scenario)
    db.commit()
    return [_row(s) for s in scenarios]


class AnswerRequest(BaseModel):
    content: str


@router.post("/reply-onboarding/scenarios/{scenario_id}/answer")
def answer_scenario(
    scenario_id: int, body: AnswerRequest, background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    scenario = _get_owned_scenario(db, scenario_id, user)
    if scenario.status == "answered":
        raise HTTPException(409, "이미 답변한 시나리오입니다")

    # 온보딩은 사장님이 직접 검토·제출한 것이므로, save_final_reply(코어
    # 설계)의 초안-대조 승격 판정과 달리 diff 비교 없이 항상 승격한다.
    db.add(GoldenExample(
        store_id=scenario.store_id, category=scenario.category,
        review_text=scenario.virtual_review_text, reply_text=body.content,
        is_manual=True, is_synthetic=False, source="onboarding",
        created_at=datetime.now(timezone.utc),
    ))
    scenario.status = "answered"
    db.commit()
    background_tasks.add_task(refresh_store_style_profile_background, scenario.store_id)
    return _row(scenario)


@router.post("/reply-onboarding/scenarios/{scenario_id}/skip")
def skip_scenario(
    scenario_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    scenario = _get_owned_scenario(db, scenario_id, user)
    if scenario.status == "answered":
        raise HTTPException(409, "이미 답변한 시나리오입니다")
    scenario.status = "skipped"
    db.commit()
    return _row(scenario)
```

`backend/app/main.py`를 수정한다. 임포트 줄을 바꾸고:

```python
from app.routers import ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections
```

`app.include_router(reply_settings.router)` 다음 줄에 추가:

```python
app.include_router(reply_onboarding.router)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_reply_onboarding.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 스위트 회귀 확인**

Run: `cd backend && python -m pytest -q`
Expected: 기존 테스트 전부 PASS + 이번 태스크 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/reply_onboarding.py backend/app/main.py backend/tests/test_reply_onboarding.py
git commit -m "feat: 온보딩 마법사/트리클/답변/건너뛰기 API 추가"
```

---

### Task 5: 프론트엔드 — 대시보드 트리클 카드 + 가게 연결 마법사 모달

**Files:**
- Modify: `frontend/src/app/(app)/dashboard/page.tsx`
- Modify: `frontend/src/app/(app)/account/stores/page.tsx`

**Interfaces:**
- Consumes: `GET /reply-onboarding/today`, `POST /reply-onboarding/wizard`, `POST /reply-onboarding/scenarios/{id}/answer`, `POST /reply-onboarding/scenarios/{id}/skip`(Task 4) — 응답 형태 `{id: number, category: string, virtual_review_text: string, draft_text: string, status: string}`.
- Produces: 없음 (최상위 페이지 컴포넌트)

이 태스크는 백엔드 pytest 대상이 아니다 — 이 프로젝트의 프론트엔드에는 자동화 테스트가 없다(`npm test` 스크립트 없음, `package.json` 확인됨). 검증은 TypeScript 빌드(`next build`가 타입 체크를 포함)와 dev 서버에서의 직접 브라우저 확인으로 한다.

- [ ] **Step 1: 대시보드에 "오늘의 답글 훈련" 카드 추가**

`frontend/src/app/(app)/dashboard/page.tsx` 맨 위 임포트 줄을 바꾼다:

```tsx
import { apiGet, apiPost, percent, won } from "@/lib/api";
```

`ALERT_LABEL` 상수 선언 바로 아래에 카테고리 라벨 상수와 타입을 추가한다:

```tsx
type OnboardingScenario = {
  id: number;
  category: string;
  virtual_review_text: string;
  draft_text: string;
  status: string;
};

const ONBOARDING_CATEGORY_LABEL: Record<string, string> = {
  food_quality: "음식 품질(맛/온도/양)",
  delivery: "배달(지연/파손)",
  hygiene: "위생/이물질",
  service: "응대",
  price: "가격",
  missing_or_wrong_item: "오배송/누락",
};
```

`ClickableCard` 함수 정의 뒤, `UgacleModal` 함수 정의 앞에 새 컴포넌트를 추가한다:

```tsx
function OnboardingTrainingCard({ storeId }: { storeId: number }) {
  const [scenarios, setScenarios] = useState<OnboardingScenario[] | null>(null);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<OnboardingScenario[]>(`/reply-onboarding/today?store_id=${storeId}`).then(setScenarios);
  }, [storeId]);

  const current = scenarios?.[index] ?? null;

  useEffect(() => {
    if (current) setDraft(current.draft_text);
  }, [current]);

  if (!scenarios || scenarios.length === 0 || !current) return null;

  const advance = () => {
    if (index + 1 < scenarios.length) {
      setIndex(index + 1);
    } else {
      setScenarios([]);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reply-onboarding/scenarios/${current.id}/answer`, { content: draft });
      advance();
    } finally {
      setSaving(false);
    }
  };

  const skip = async () => {
    setSaving(true);
    try {
      await apiPost(`/reply-onboarding/scenarios/${current.id}/skip`);
      advance();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={`오늘의 답글 훈련 (${index + 1}/${scenarios.length})`}>
      <p className="mb-3 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        아직 &quot;{ONBOARDING_CATEGORY_LABEL[current.category] ?? current.category}&quot; 유형의 실제 답글이 없어요.
        아래 예시 리뷰에 답글을 다듬어 저장하면, 이후 비슷한 리뷰에 사장님 말투로 답글이 생성돼요.
      </p>
      <p className="mb-2 text-sm text-foreground">{current.virtual_review_text}</p>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        disabled={saving}
        className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          onClick={skip}
          disabled={saving}
          className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface-2 disabled:opacity-60"
        >
          건너뛰기
        </button>
        <button
          onClick={save}
          disabled={saving || !draft.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          저장
        </button>
      </div>
    </Card>
  );
}
```

`DashboardPage`의 return 안, "답글 대기 리뷰"/"알림" 그리드(`<div className="grid grid-cols-1 gap-4 lg:grid-cols-2">...</div>`) 바로 다음, 모달들이 시작되기 전에 추가:

```tsx
      <OnboardingTrainingCard storeId={storeId} />

```

- [ ] **Step 2: 가게 연결 화면에 배민 로그인 직후 마법사 모달 추가**

`frontend/src/app/(app)/account/stores/page.tsx`에서 `apiPost` 임포트는 이미 있으므로 그대로 두고, `SyncStatus` 타입 선언 아래에 온보딩 타입을 추가한다:

```tsx
type OnboardingScenario = {
  id: number;
  category: string;
  virtual_review_text: string;
  draft_text: string;
  status: string;
};

const ONBOARDING_CATEGORY_LABEL: Record<string, string> = {
  food_quality: "음식 품질(맛/온도/양)",
  delivery: "배달(지연/파손)",
  hygiene: "위생/이물질",
  service: "응대",
  price: "가격",
  missing_or_wrong_item: "오배송/누락",
};
```

`connecting`/`modalError` state 선언 아래에 마법사 state를 추가:

```tsx
  const [wizardScenarios, setWizardScenarios] = useState<OnboardingScenario[] | null>(null);
  const [wizardIndex, setWizardIndex] = useState(0);
  const [wizardDraft, setWizardDraft] = useState("");
  const [wizardSaving, setWizardSaving] = useState(false);
```

`submitLogin` 함수에서 배민 로그인 성공 직후(`await apiPost(...)` 다음 줄)에 마법사를 트리거하도록 바꾼다. 기존:

```tsx
      if (loginTarget.code === "baemin") {
        await apiPost(`/store-connections/baemin/login?store_id=${storeId}`, {
          platform_login_id: loginId,
          platform_login_password: loginPw,
        });
      } else {
```

다음으로 교체:

```tsx
      if (loginTarget.code === "baemin") {
        await apiPost(`/store-connections/baemin/login?store_id=${storeId}`, {
          platform_login_id: loginId,
          platform_login_password: loginPw,
        });
        const scenarios = await apiPost<OnboardingScenario[]>(`/reply-onboarding/wizard?store_id=${storeId}`);
        if (scenarios.length > 0) {
          setWizardScenarios(scenarios);
          setWizardIndex(0);
          setWizardDraft(scenarios[0].draft_text);
        }
      } else {
```

마법사 상태가 바뀔 때 텍스트 영역을 그 시나리오의 초안으로 채우는 함수와 진행 로직을 `submitLogin` 아래, `disconnect` 함수 위에 추가:

```tsx
  const wizardCurrent = wizardScenarios?.[wizardIndex] ?? null;

  const advanceWizard = () => {
    if (!wizardScenarios) return;
    if (wizardIndex + 1 < wizardScenarios.length) {
      const next = wizardIndex + 1;
      setWizardIndex(next);
      setWizardDraft(wizardScenarios[next].draft_text);
    } else {
      setWizardScenarios(null);
    }
  };

  const saveWizardAnswer = async () => {
    if (!wizardCurrent) return;
    setWizardSaving(true);
    try {
      await apiPost(`/reply-onboarding/scenarios/${wizardCurrent.id}/answer`, { content: wizardDraft });
      advanceWizard();
    } finally {
      setWizardSaving(false);
    }
  };

  const skipWizardScenario = async () => {
    if (!wizardCurrent) return;
    setWizardSaving(true);
    try {
      await apiPost(`/reply-onboarding/scenarios/${wizardCurrent.id}/skip`);
      advanceWizard();
    } finally {
      setWizardSaving(false);
    }
  };
```

마지막으로, 기존 로그인 `Modal` JSX 바로 뒤(컴포넌트 return의 최상위 `<div>` 안, 닫는 태그 직전)에 마법사 모달을 추가:

```tsx
      {wizardCurrent && (
        <Modal
          title={`답글 스타일 빠르게 설정하기 (${wizardIndex + 1}/${wizardScenarios!.length})`}
          onClose={() => setWizardScenarios(null)}
        >
          <div className="space-y-4">
            <p className="text-xs text-muted">
              &quot;{ONBOARDING_CATEGORY_LABEL[wizardCurrent.category] ?? wizardCurrent.category}&quot; 유형의 예시
              리뷰예요. 사장님 말투로 답글을 다듬어 저장하면 이후 비슷한 리뷰에 바로 활용됩니다. 언제든 닫아도
              괜찮아요 — 남은 항목은 대시보드에서 나중에 다시 볼 수 있습니다.
            </p>
            <p className="text-sm text-foreground">{wizardCurrent.virtual_review_text}</p>
            <textarea
              value={wizardDraft}
              onChange={(e) => setWizardDraft(e.target.value)}
              rows={4}
              disabled={wizardSaving}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={skipWizardScenario}
                disabled={wizardSaving}
                className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface-2 disabled:opacity-60"
              >
                건너뛰기
              </button>
              <button
                onClick={saveWizardAnswer}
                disabled={wizardSaving || !wizardDraft.trim()}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                저장
              </button>
            </div>
          </div>
        </Modal>
      )}
```

- [ ] **Step 3: 빌드로 타입 오류 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 빌드 성공 (타입 오류 시 위 코드의 필드명/타입을 다시 확인)

- [ ] **Step 4: dev 서버에서 직접 확인**

Run: `cd frontend && npm run dev` (백엔드도 `ANTHROPIC_API_KEY` 없이 실행 중이면 온보딩 API가 500을 낼 수 있으니, 로컬에서 확인할 때는 `ANTHROPIC_API_KEY`가 설정된 백엔드로 확인하거나 Task 4 테스트의 monkeypatch처럼 임시로 `app/llm/onboarding.py`/`app/llm/generate.py`의 `client.call_haiku`/`call_sonnet`를 더미로 바꿔 수동 확인)

- 대시보드 진입 시 golden_examples가 비어있는 매장이면 "오늘의 답글 훈련" 카드가 나타나는지, 저장/건너뛰기 후 다음 항목으로 넘어가는지, 6개를 다 처리하면 카드가 사라지는지 확인한다.
- "가게 연결" 화면에서 배민 실계정으로 로그인 성공 시 마법사 모달이 뜨는지, 진행률 표시가 맞는지, 중간에 닫아도 남은 항목이 대시보드 트리클에 나중에 나오는지 확인한다.

- [ ] **Step 5: 커밋**

```bash
git add "frontend/src/app/(app)/dashboard/page.tsx" "frontend/src/app/(app)/account/stores/page.tsx"
git commit -m "feat: 온보딩 대시보드 트리클 카드 + 가게 연결 마법사 모달 추가"
```

---

## Self-Review 메모 (플랜 작성자용, 실행 시 참고만)

- **스펙 커버리지**: 설계 문서의 DB 스키마(Task 1), 커버리지 스캔/시나리오 생성(Task 2), 가상 리뷰 생성(Task 2), 4개 API(Task 4), 범용 시드 스크립트(Task 3), 프론트엔드 대시보드 카드 + 마법사 모달(Task 5) 전부 태스크로 매핑됨. 테스트 계획의 항목(재사용/미저장/wizard 빈 배열/today 최대 3개+안정성/answer 승격+diff무관/skip 재대상화/시드 멱등성)도 전부 테스트로 매핑됨.
- **플레이스홀더 스캔**: "TODO"/"나중에" 등 없음. 모든 코드 블록이 실제 실행 가능한 완성 코드.
- **타입/시그니처 일관성**: `get_or_create_scenario(db, store, category)`가 Task 2와 Task 4 전체에서 동일한 시그니처로 쓰임. `CATEGORY_LABELS`(Task 2 Step 1에서 rename) 이름이 Task 2 본문 전체에서 일관됨. 프론트엔드 `OnboardingScenario` 타입 필드명(`id/category/virtual_review_text/draft_text/status`)이 Task 4의 `_row()` 응답과 Task 5의 두 파일에서 동일함.
