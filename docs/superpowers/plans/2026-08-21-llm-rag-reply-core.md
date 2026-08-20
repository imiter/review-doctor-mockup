# LLM + RAG 답글 생성 — 코어 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 문제 리뷰(불만이 감지된 리뷰)에 실제 Claude API 기반 RAG 답글
생성을 도입한다 — 분류(Haiku) → 골든 예시 검색(SQL 필터) → 생성(Sonnet)
→ 사장님 검토·저장 → 새 골든 예시로 승격되는 파이프라인 전체.

**Architecture:** `backend/app/llm/` 신규 패키지(client/classify/rag/
generate/style_profile)가 Anthropic API를 감싸고, `review_sync.py`가
리뷰 동기화 시점에 분류를 호출하며, `reviews.py` 라우터가 문제
리뷰인지에 따라 기존 템플릿 경로와 신규 RAG 경로를 분기한다. 긍정
리뷰(문제 신호 없음) 경로는 전혀 건드리지 않는다.

**Tech Stack:** FastAPI, SQLAlchemy, Anthropic Python SDK(신규 의존성),
`claude-haiku-4-5-20251001`(분류), `claude-sonnet-5`(생성/스타일 추출).

**참고 문서:** `docs/superpowers/specs/2026-08-21-llm-rag-reply-design.md`
(전체 설계, 실측 데이터 확인 내용, 시스템 프롬프트 초안의 원본).

이 플랜은 설계 문서의 범위 중 **온보딩 데이터 부트스트랩
(`onboarding_scenarios`, "오늘의 답글 훈련" UI)은 제외**한다 — 온보딩은
이 플랜이 만드는 `golden_examples`/분류 인프라가 먼저 존재해야 의미가
있으므로, 이 플랜이 배포된 뒤 별도 플랜(`2026-08-21-llm-rag-reply-
onboarding.md`, 아직 작성 전)으로 이어간다.

## Global Constraints

- 새 텍스트/에러 메시지는 기존 코드베이스 관례대로 한국어로 작성한다.
- 긍정 리뷰(`category == "no_issue"`) 경로는 기존 4-페르소나 템플릿
  그대로 유지 — 이번 플랜에서 절대 변경하지 않는다.
- 분류(Haiku)와 생성/스타일 추출(Sonnet)은 반드시 모델을 분리한다 —
  비용 최적화가 설계 요구사항이다.
- 벡터 검색(pgvector 등)은 도입하지 않는다 — 골든 예시 검색은 SQL
  `category` 필터 + `LIMIT`만 쓴다.
- `store_style_profile` 재생성 쿼리는 반드시
  `is_manual = true AND is_synthetic = false`로 제한한다 — 가상 데이터로
  스타일을 추출하면 안 된다(순환 오염 방지).
- few-shot 시스템 프롬프트에는 반드시 "예시는 스타일만 참고, 사건 내용
  복사 금지" 지시를 포함한다.
- Anthropic API 호출은 항상 `backend/app/llm/client.py`의
  `call_haiku`/`call_sonnet` 두 함수를 통해서만 하고, 호출부는 그 함수를
  가져올 때 `from app.llm import client`로 모듈째 가져와 `client.call_haiku(...)`
  형태로 호출한다(bare-name import 금지) — 그래야 테스트에서
  `monkeypatch.setattr(client, "call_haiku", ...)`가 모든 호출부에 동시에
  반영된다.
- `ANTHROPIC_API_KEY`가 없거나 API 호출이 실패해도 리뷰 동기화 자체는
  절대 막히면 안 된다 — 분류 실패는 조용히 기본값(`no_issue`)으로
  폴백한다.

---

### Task 1: DB 스키마 — `reviews` 확장 + `golden_examples` + `store_style_profile` + `alerts` CHECK 확장

**Files:**
- Modify: `schema.sql` (`reviews` 테이블 정의, `alerts` 테이블 정의 뒤)
- Modify: `backend/app/models.py` (`Review` 클래스, 새 모델 2개 추가)
- Test: `backend/tests/test_llm_models.py` (신규 파일)

**Interfaces:**
- Produces:
  ```python
  Review.category: Mapped[str]            # 기본값 "no_issue"
  Review.is_sensitive: Mapped[bool]        # 기본값 False
  Review.sentiment_conflict: Mapped[bool]  # 기본값 False

  class GoldenExample(Base):
      __tablename__ = "golden_examples"
      id, store_id, category, review_text, reply_text,
      is_manual, is_synthetic, source, source_review_id, source_reply_id, created_at

  class StoreStyleProfile(Base):
      __tablename__ = "store_style_profile"
      store_id (PK), rules, generated_from_count, updated_at
  ```
  이후 모든 태스크가 이 세 가지를 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_models.py` 새로 생성:

```python
from datetime import datetime, timezone

from app.models import GoldenExample, Review, StoreStyleProfile


def test_review_classification_columns_default(db_session, seeded_user, platforms):
    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=5, content="맛있어요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    row = db_session.query(Review).filter_by(id=review.id).one()
    assert row.category == "no_issue"
    assert row.is_sensitive is False
    assert row.sentiment_conflict is False


def test_golden_example_round_trips(db_session, seeded_user):
    ex = GoldenExample(
        store_id=seeded_user["store"].id, category="hygiene",
        review_text="이물질이 나왔어요", reply_text="죄송합니다, 확인하겠습니다",
        is_manual=True, is_synthetic=False, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    db_session.commit()

    row = db_session.query(GoldenExample).filter_by(id=ex.id).one()
    assert row.category == "hygiene"
    assert row.is_manual is True
    assert row.source == "backfill"


def test_store_style_profile_round_trips(db_session, seeded_user):
    profile = StoreStyleProfile(
        store_id=seeded_user["store"].id, rules="- 구체적 원인을 설명한다\n- 재방문 고객을 언급한다",
        generated_from_count=5, updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    row = db_session.query(StoreStyleProfile).filter_by(store_id=seeded_user["store"].id).one()
    assert row.generated_from_count == 5
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_models.py -v`
Expected: FAIL — `Review.category`/`GoldenExample`/`StoreStyleProfile`가
아직 없어 `AttributeError` 또는 `ImportError`.

- [ ] **Step 3: `schema.sql` 수정**

`reviews` 테이블 정의(현재 151-166줄)의 `status` 컬럼 다음에 추가:

```sql
    status               VARCHAR(12) NOT NULL DEFAULT 'unanswered'
                         CHECK (status IN ('unanswered', 'pending', 'answered')),
    category             VARCHAR(24) NOT NULL DEFAULT 'no_issue'
                         CHECK (category IN (
                             'food_quality', 'delivery', 'hygiene', 'service',
                             'price', 'missing_or_wrong_item', 'no_issue'
                         )),
    is_sensitive         BOOLEAN     NOT NULL DEFAULT FALSE,
    sentiment_conflict   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_reviews_category ON reviews(store_id, category);
```

(`created_at`/닫는 `);`은 기존 그대로 두고 그 사이에 삽입 — 전체 블록을
위처럼 교체하면 된다.)

`alerts` 테이블 정의(현재 295-303줄) 바로 뒤, `social_accounts` 섹션
시작 전에 아래 두 테이블을 새로 추가:

```sql
-- ----------------------------------------------------------------------------
-- 16-1. golden_examples — RAG few-shot 소스. 사장님이 직접 쓰거나 승인한
--       진짜 답글(is_manual=true)과, 예시가 부족할 때만 보충하는 순수
--       AI 생성 모범답안(is_synthetic=true)을 함께 담는다. 검색은 이
--       테이블 하나만 필터링하면 끝나야 한다(조인 없음).
-- ----------------------------------------------------------------------------
CREATE TABLE golden_examples (
    id               BIGSERIAL PRIMARY KEY,
    store_id         BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category         VARCHAR(24)  NOT NULL,
    review_text      TEXT         NOT NULL,
    reply_text       TEXT         NOT NULL,
    is_manual        BOOLEAN      NOT NULL,
    is_synthetic     BOOLEAN      NOT NULL,
    source           VARCHAR(16)  NOT NULL
                     CHECK (source IN ('backfill', 'organic', 'onboarding', 'synthetic')),
    source_review_id BIGINT       REFERENCES reviews(id) ON DELETE SET NULL,
    source_reply_id  BIGINT       REFERENCES review_replies(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_golden_examples_lookup
    ON golden_examples(store_id, category, is_manual, is_synthetic, created_at DESC);

-- ----------------------------------------------------------------------------
-- 16-2. store_style_profile — 매장별 답글 스타일 규칙 캐싱. golden_examples
--       중 is_manual=true AND is_synthetic=false인 데이터로만 재생성한다
--       (가상 데이터로 스타일을 뽑으면 AI가 자기 산출물을 학습하는
--       순환 오염이 생긴다).
-- ----------------------------------------------------------------------------
CREATE TABLE store_style_profile (
    store_id             BIGINT       PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
    rules                TEXT         NOT NULL,
    generated_from_count INT          NOT NULL,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

`alerts` 테이블 정의의 `alert_type` CHECK(현재 298-299줄)를 교체:

```sql
    alert_type VARCHAR(20)  NOT NULL
               CHECK (alert_type IN ('negative_review', 'unanswered_review', 'rank_drop', 'sensitive_review')),
```

- [ ] **Step 4: `backend/app/models.py` 수정**

`Review` 클래스(현재 169-189줄)의 `status` 필드 다음에 추가:

```python
    status: Mapped[str] = mapped_column(String(12), default="unanswered")
    category: Mapped[str] = mapped_column(String(24), default="no_issue")
    is_sensitive: Mapped[bool] = mapped_column(default=False)
    sentiment_conflict: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
```

`ReviewReply` 클래스 다음(현재 222-223줄 사이 정도, `DailySettlement`
클래스 시작 전)에 새 클래스 2개 추가:

```python
class GoldenExample(Base):
    __tablename__ = "golden_examples"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    review_text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text)
    is_manual: Mapped[bool]
    is_synthetic: Mapped[bool]
    source: Mapped[str] = mapped_column(String(16))
    source_review_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    source_reply_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("review_replies.id"))
    created_at: Mapped[datetime]


class StoreStyleProfile(Base):
    __tablename__ = "store_style_profile"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    rules: Mapped[str] = mapped_column(Text)
    generated_from_count: Mapped[int]
    updated_at: Mapped[datetime]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 기존 테스트 전부 그대로 통과 — 새 컬럼은 전부 기본값이 있어
기존 `Review(...)` 생성 코드를 깨지 않는다.

- [ ] **Step 7: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_llm_models.py
git commit -m "feat: reviews 분류 컬럼 + golden_examples/store_style_profile 테이블 추가"
```

> **배포 노트**: 이 태스크가 머지된 뒤 실제 Railway Postgres에는 위
> `ALTER TABLE`/`CREATE TABLE`/CHECK 재정의 SQL을 수동으로 실행해야
> 한다(Alembic 없이 schema.sql이 정본 — CLAUDE.md 참고). 이 실행은
> 플랜을 조율하는 에이전트가 최종 배포 시점에 직접 한다.

---

### Task 2: Anthropic 클라이언트 래퍼 + 리뷰 분류 (Haiku)

**Files:**
- Create: `backend/app/llm/__init__.py` (빈 파일)
- Create: `backend/app/llm/client.py`
- Create: `backend/app/llm/classify.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_llm_classify.py` (신규 파일)

**Interfaces:**
- Produces:
  ```python
  # app/llm/client.py
  HAIKU_MODEL: str
  SONNET_MODEL: str
  def call_haiku(system: str, user: str, *, max_tokens: int = 300) -> str
  def call_sonnet(system: str, user: str, *, max_tokens: int = 1000) -> str

  # app/llm/classify.py
  VALID_CATEGORIES: tuple[str, ...]
  class ClassificationError(Exception)

  @dataclass(frozen=True)
  class ReviewClassification:
      category: str
      is_sensitive: bool
      sentiment_conflict: bool

  def classify_review(content: str, rating: int) -> ReviewClassification
  ```
  Task 3이 `classify_review`/`ClassificationError`를, Task 6/7이
  `client.call_sonnet`을 그대로 쓴다.

- [ ] **Step 1: 의존성 추가**

`backend/requirements.txt` 맨 아래에 한 줄 추가:

```
anthropic
```

Run: `cd backend && .venv/bin/pip install -r requirements.txt`

- [ ] **Step 2: `backend/app/llm/__init__.py` 생성**

빈 파일로 생성(패키지 마커).

- [ ] **Step 3: `backend/app/llm/client.py` 작성**

```python
"""Anthropic API 얇은 래퍼 — 이 프로젝트에서 처음 도입하는 실제 외부 AI
호출이다(Claude Pro 구독과는 별도로 과금되는 API 키가 필요하다 —
ANTHROPIC_API_KEY 환경변수, console.anthropic.com에서 발급). 분류/생성/
스타일 추출 각 모듈은 이 파일의 두 함수만 통해 Anthropic API를 호출한다
— 테스트에서 이 두 함수만 monkeypatch하면 실제 API 호출 없이 전부
검증할 수 있다."""

import os

import anthropic

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-5"


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def call_haiku(system: str, user: str, *, max_tokens: int = 300) -> str:
    response = _client().messages.create(
        model=HAIKU_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def call_sonnet(system: str, user: str, *, max_tokens: int = 1000) -> str:
    response = _client().messages.create(
        model=SONNET_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text
```

- [ ] **Step 4: 실패하는 테스트 작성 — `classify_review`**

`backend/tests/test_llm_classify.py` 새로 생성:

```python
import pytest

from app.llm import classify


def test_classify_review_parses_valid_response(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "hygiene", "is_sensitive": true, "sentiment_conflict": false}',
    )
    result = classify.classify_review("이물질이 나왔어요", 5)
    assert result.category == "hygiene"
    assert result.is_sensitive is True
    assert result.sentiment_conflict is False


def test_classify_review_no_issue_case(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "no_issue", "is_sensitive": false, "sentiment_conflict": false}',
    )
    result = classify.classify_review("정말 맛있어요!", 5)
    assert result.category == "no_issue"


def test_classify_review_raises_on_invalid_category(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "unknown_thing", "is_sensitive": false, "sentiment_conflict": false}',
    )
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_raises_on_malformed_json(monkeypatch):
    monkeypatch.setattr(classify.client, "call_haiku", lambda system, user, **kw: "not json")
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_raises_when_api_call_fails(monkeypatch):
    def _raise(system, user, **kw):
        raise RuntimeError("네트워크 오류")

    monkeypatch.setattr(classify.client, "call_haiku", _raise)
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)
```

- [ ] **Step 5: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.classify'`

- [ ] **Step 6: `backend/app/llm/classify.py` 작성**

```python
"""리뷰 자동 분류 — 불만 유형(category), 민감도(is_sensitive), 별점-텍스트
불일치(sentiment_conflict)를 Haiku 1회 호출로 함께 판정한다. 리뷰가
동기화되는 시점(review_sync.py)에 호출돼야 사장님이 리뷰를 열어보지
않아도 민감 리뷰 알림이 뜬다."""

import json
from dataclasses import dataclass

from app.llm import client

VALID_CATEGORIES = (
    "food_quality", "delivery", "hygiene", "service",
    "price", "missing_or_wrong_item", "no_issue",
)

_SYSTEM_PROMPT = """너는 배달 음식점 리뷰를 분석하는 분류기다. 아래 리뷰를 읽고 JSON으로만 답하라.

카테고리(정확히 하나만 선택):
- food_quality: 맛, 온도, 양에 대한 불만
- delivery: 배달 지연, 파손에 대한 불만
- hygiene: 위생, 이물질, 곰팡이 등 안전 관련 불만
- service: 응대, 태도에 대한 불만
- price: 가격에 대한 불만
- missing_or_wrong_item: 누락, 오배송
- no_issue: 위 어디에도 해당하는 불만이 없음 (칭찬만 있거나 중립적)

is_sensitive: 위생/이물질/알레르기/안전 관련 언급이 있어 신중한 대응이
필요하면 true. 단순 맛 불만 등은 false.

sentiment_conflict: 별점과 리뷰 내용의 감정이 서로 어긋나면 true. 예:
별점은 4~5점인데 내용에 뚜렷한 불만이 섞여 있는 경우. 별점이 낮은데
내용도 부정적인 건 "일치"이므로 false.

JSON 형식으로만 답하라: {"category": "...", "is_sensitive": true/false, "sentiment_conflict": true/false}"""


class ClassificationError(Exception):
    pass


@dataclass(frozen=True)
class ReviewClassification:
    category: str
    is_sensitive: bool
    sentiment_conflict: bool


def classify_review(content: str, rating: int) -> ReviewClassification:
    user_message = f'리뷰: "{content}"\n별점: {rating}'
    try:
        raw = client.call_haiku(_SYSTEM_PROMPT, user_message)
        data = json.loads(raw)
    except Exception as e:
        raise ClassificationError(f"리뷰 분류 API 호출 실패: {e}") from e

    category = data.get("category")
    if category not in VALID_CATEGORIES:
        raise ClassificationError(f"알 수 없는 category: {category!r}")
    return ReviewClassification(
        category=category,
        is_sensitive=bool(data.get("is_sensitive", False)),
        sentiment_conflict=bool(data.get("sentiment_conflict", False)),
    )
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_classify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 8: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 9: 커밋**

```bash
git add backend/requirements.txt backend/app/llm/__init__.py backend/app/llm/client.py backend/app/llm/classify.py backend/tests/test_llm_classify.py
git commit -m "feat: Anthropic 클라이언트 래퍼 + 리뷰 분류(Haiku) 추가"
```

---

### Task 3: 리뷰 동기화에 분류 연동 + 민감 리뷰 알림

**Files:**
- Modify: `backend/app/review_sync.py:1-30` (import), `:313-330` (INSERT 루프)
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 2의 `classify_review(content, rating) -> ReviewClassification`, `ClassificationError`
- Produces: 없음(이 태스크는 통합만) — 하지만 `Review.category`/`is_sensitive`/
  `sentiment_conflict`가 실제 동기화 경로에서 채워진다는 계약을 확정한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_review_sync.py`에 이어서 추가(파일 상단 import에
`from app.models import Alert`를 추가):

```python
def test_sync_classifies_new_review_and_stores_result(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1, _RAW_2])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=1001).one()
    assert review.category == "food_quality"


def test_sync_creates_sensitive_alert_for_flagged_review(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import Alert

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )

    sync_reviews_for_job(job, conn, db_session)

    alert = db_session.query(Alert).filter_by(store_id=job.store_id, alert_type="sensitive_review").one()
    assert "확인" in alert.message


def test_sync_falls_back_to_default_category_when_classification_fails(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])

    def _raise(content, rating):
        raise review_sync_mod.ClassificationError("API 다운")

    monkeypatch.setattr(review_sync_mod, "classify_review", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"  # 분류 실패가 동기화 자체를 막지 않는다
    review = db_session.query(Review).filter_by(external_review_id=1001).one()
    assert review.category == "no_issue"
    assert review.is_sensitive is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -k "classif or sensitive_alert" -v`
Expected: FAIL — `review_sync_mod.classify_review`/`ClassificationError`가
아직 없어 `AttributeError`.

- [ ] **Step 3: `backend/app/review_sync.py` 수정**

import 섹션(현재 16-27줄 `from app.models import (...)`)에 `Alert` 추가:

```python
from app.models import (
    AdCampaign,
    Alert,
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    Order,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
```

그 아래(현재 28-30줄 scraper import들) 다음 줄에 추가:

```python
from app.llm.classify import ClassificationError, classify_review
```

INSERT 루프(현재 313-330줄)를 아래로 교체:

```python
            for raw, m in mapped_with_raw:
                if m["external_review_id"] in existing_ids:
                    continue
                review = Review(**m)
                try:
                    classification = classify_review(review.content, review.rating)
                    review.category = classification.category
                    review.is_sensitive = classification.is_sensitive
                    review.sentiment_conflict = classification.sentiment_conflict
                except ClassificationError:
                    # 분류 실패해도 리뷰 저장 자체는 막지 않는다 — 컬럼
                    # 기본값(no_issue)으로 남기고 계속 진행한다. 리뷰
                    # 동기화가 AI 분류 가용성에 발목잡히면 안 된다.
                    pass
                db.add(review)
                # review_replies가 review_id FK로 참조하려면 실제 id가
                # 필요하다 — autoflush=False(app.db.SessionLocal)라 명시적으로
                # flush해야 방금 만든 review의 id가 채워진다.
                db.flush()
                if review.is_sensitive:
                    db.add(Alert(
                        store_id=job.store_id, alert_type="sensitive_review",
                        message=f"민감한 리뷰가 감지됐습니다: {review.menu_summary} 관련 — 우선 확인이 필요합니다",
                        created_at=datetime.now(timezone.utc),
                    ))
                owner_reply = extract_owner_reply(raw)
                if owner_reply is not None:
                    reply_content, replied_at = owner_reply
                    db.add(ReviewReply(
                        review_id=review.id, reply_type="final", style_id=None,
                        content=reply_content, created_at=replied_at,
                    ))
                existing_ids.add(m["external_review_id"])
                total_inserted += 1
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v`
Expected: PASS 전체(기존 테스트 포함 회귀 없음 — `sync_setup` 픽스처가
`classify_review`를 monkeypatch하지 않는 기존 테스트들은 실제
`ANTHROPIC_API_KEY`가 없어 `ClassificationError`로 폴백하고 `no_issue`
기본값으로 통과한다).

- [ ] **Step 5: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 리뷰 동기화에 자동 분류 연동 + 민감 리뷰 알림 생성"
```

---

### Task 4: 기존 부정 리뷰 5건 골든 예시 백필

**Files:**
- Create: `backend/scripts/backfill_golden_examples.py`
- Test: `backend/tests/test_backfill_golden_examples.py` (신규 파일)

**Interfaces:**
- Consumes: Task 1의 `GoldenExample` 모델, Task 2의
  `classify_review(content, rating) -> ReviewClassification`
- Produces: `backfill_negative_review_replies(db: Session, store_id: int) -> int`
  (반환값 = 새로 넣은 golden_examples 행 수) — 커맨드라인에서
  `python -m backend.scripts.backfill_golden_examples <store_id>`로도
  실행 가능해야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_backfill_golden_examples.py` 새로 생성:

```python
from datetime import datetime, timezone

from app.llm.classify import ReviewClassification
from app.models import GoldenExample, Review, ReviewReply
from scripts.backfill_golden_examples import backfill_negative_review_replies


def _make_answered_review(db_session, store_id, platform_id, *, rating, content, reply_content):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="치킨", rating=rating,
        content=content, customer_nickname="손님", status="answered",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewReply(
        review_id=review.id, reply_type="final", style_id=None,
        content=reply_content, created_at=datetime.now(timezone.utc),
    ))
    return review


def test_backfill_creates_golden_example_for_each_low_rating_reply(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", reply_content="확인해서 조치하겠습니다",
    )
    db_session.commit()

    count = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert count == 1
    example = db_session.query(GoldenExample).one()
    assert example.category == "hygiene"
    assert example.is_manual is True
    assert example.is_synthetic is False
    assert example.source == "backfill"
    assert example.reply_text == "확인해서 조치하겠습니다"


def test_backfill_skips_reviews_above_threshold_rating(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="food_quality", is_sensitive=False, sentiment_conflict=False),
    )
    _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=4, content="맛있어요", reply_content="감사합니다",
    )
    db_session.commit()

    count = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert count == 0
    assert db_session.query(GoldenExample).count() == 0


def test_backfill_is_idempotent(db_session, seeded_user, platforms, monkeypatch):
    from scripts import backfill_golden_examples as backfill_mod

    monkeypatch.setattr(
        backfill_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="hygiene", is_sensitive=True, sentiment_conflict=False),
    )
    review = _make_answered_review(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        rating=1, content="이물질이 나왔어요", reply_content="확인해서 조치하겠습니다",
    )
    db_session.commit()

    first = backfill_negative_review_replies(db_session, seeded_user["store"].id)
    second = backfill_negative_review_replies(db_session, seeded_user["store"].id)

    assert first == 1
    assert second == 0  # 같은 review에 대해 두 번 넣지 않는다
    assert db_session.query(GoldenExample).filter_by(source_review_id=review.id).count() == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_backfill_golden_examples.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_golden_examples'`

- [ ] **Step 3: `backend/scripts/backfill_golden_examples.py` 작성**

`backend/scripts/` 디렉터리가 없으면 먼저 만든다(`backend/scripts/__init__.py`
빈 파일도 함께 생성).

```python
"""브레인스토밍 중 확인한, 사장님이 이미 실제로 작성한 별점 1~2점(부정)
리뷰 답글을 golden_examples로 백필한다. 이 스크립트가 대상으로 삼는
건 review_replies.reply_type='final'이 이미 있는 별점 1~2점 리뷰뿐이다
— 별점 3점 이상은 사장님이 직접 확인해주지 않아 "진짜 본인 목소리"인지
확신할 수 없으므로 이번 백필 대상에서 제외한다(설계 문서 2026-08-21
참고). 여러 번 실행해도 안전하다(같은 review_id는 중복 삽입하지 않음)."""

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.classify import ClassificationError, classify_review
from app.models import GoldenExample, Review, ReviewReply

_MAX_RATING = 2


def backfill_negative_review_replies(db, store_id: int) -> int:
    candidates = db.scalars(
        select(Review).where(Review.store_id == store_id, Review.rating <= _MAX_RATING)
    ).all()

    inserted = 0
    for review in candidates:
        already = db.scalar(
            select(GoldenExample).where(GoldenExample.source_review_id == review.id)
        )
        if already is not None:
            continue

        final_reply = db.scalar(
            select(ReviewReply).where(ReviewReply.review_id == review.id, ReviewReply.reply_type == "final")
        )
        if final_reply is None:
            continue

        try:
            classification = classify_review(review.content, review.rating)
            category = classification.category
        except ClassificationError:
            continue

        db.add(GoldenExample(
            store_id=store_id, category=category,
            review_text=review.content, reply_text=final_reply.content,
            is_manual=True, is_synthetic=False, source="backfill",
            source_review_id=review.id, source_reply_id=final_reply.id,
            created_at=final_reply.created_at,
        ))
        inserted += 1

    db.commit()
    return inserted


if __name__ == "__main__":
    store_id = int(sys.argv[1])
    session = SessionLocal()
    try:
        count = backfill_negative_review_replies(session, store_id)
        print(f"{count}건의 골든 예시를 백필했습니다.")
    finally:
        session.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_backfill_golden_examples.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/scripts/__init__.py backend/scripts/backfill_golden_examples.py backend/tests/test_backfill_golden_examples.py
git commit -m "feat: 기존 부정 리뷰 실답글을 golden_examples로 백필하는 스크립트 추가"
```

> **실행 노트**: 이 스크립트는 실제 프로덕션 데이터(데모 매장의 진짜
> 부정 리뷰 5건)에 대해 배포 후 한 번 수동 실행한다
> (`python -m scripts.backfill_golden_examples <store_id>`, `ANTHROPIC_API_KEY`
> 설정된 환경에서). 이 실행은 플랜 조율 에이전트가 최종 배포 단계에서
> 담당한다 — 서브에이전트가 실제 API 비용을 발생시키며 프로덕션 데이터에
> 스크립트를 실행하는 건 이 태스크의 책임이 아니다.

---

### Task 5: RAG 검색 함수 (골든 예시 조회 + 반복 이슈 카운트)

**Files:**
- Create: `backend/app/llm/rag.py`
- Test: `backend/tests/test_llm_rag.py` (신규 파일)

**Interfaces:**
- Consumes: Task 1의 `GoldenExample`, `Review` 모델
- Produces:
  ```python
  def fetch_golden_examples(db: Session, store_id: int, category: str, limit: int = 3) -> list[GoldenExample]
  def count_recent_same_category(db: Session, store_id: int, category: str, days: int = 30) -> int
  ```
  Task 7이 두 함수를 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_rag.py` 새로 생성:

```python
from datetime import datetime, timedelta, timezone

from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, Review


def _make_example(db_session, store_id, *, category, is_manual, is_synthetic, created_at):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text="리뷰", reply_text="답글",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        created_at=created_at,
    )
    db_session.add(ex)
    return ex


def test_fetch_golden_examples_prefers_real_over_synthetic(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    real = _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", limit=3)

    assert len(result) == 1
    assert result[0].id == real.id


def test_fetch_golden_examples_backfills_with_synthetic_when_real_insufficient(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", limit=2)

    assert len(result) == 2
    assert result[0].is_manual is True
    assert result[1].is_synthetic is True


def test_fetch_golden_examples_filters_by_category(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="delivery", is_manual=True, is_synthetic=False, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", limit=3)

    assert len(result) == 1
    assert result[0].category == "delivery"


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
        customer_nickname="손님2", category="delivery", created_at=now - timedelta(days=40),  # 창 밖
    ))
    db_session.commit()

    count = count_recent_same_category(db_session, sid, "delivery", days=30)

    assert count == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_rag.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.rag'`

- [ ] **Step 3: `backend/app/llm/rag.py` 작성**

```python
"""골든 예시 검색 — 벡터 검색이 아니라 category 필터 + 최신순 LIMIT만
쓴다. 진짜 예시(is_manual=true, is_synthetic=false)를 우선하고, 부족한
만큼만 순수 AI 생성 모범답안(is_synthetic=true)으로 보충한다."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GoldenExample, Review


def fetch_golden_examples(db: Session, store_id: int, category: str, limit: int = 3) -> list[GoldenExample]:
    real = list(db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit)
    ).all())
    if len(real) >= limit:
        return real

    synthetic = list(db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_synthetic.is_(True),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit - len(real))
    ).all())
    return real + synthetic


def count_recent_same_category(db: Session, store_id: int, category: str, days: int = 30) -> int:
    return db.scalar(
        select(func.count()).select_from(Review).where(
            Review.store_id == store_id,
            Review.category == category,
            Review.created_at >= datetime.now(timezone.utc) - timedelta(days=days),
        )
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_rag.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/app/llm/rag.py backend/tests/test_llm_rag.py
git commit -m "feat: 골든 예시 검색(category 필터) + 반복 이슈 카운트 함수 추가"
```

---

### Task 6: 스타일 프로파일 재생성 (Sonnet)

**Files:**
- Create: `backend/app/llm/style_profile.py`
- Test: `backend/tests/test_llm_style_profile.py` (신규 파일)

**Interfaces:**
- Consumes: Task 1의 `StoreStyleProfile`, `GoldenExample` 모델, Task 2의
  `client.call_sonnet`
- Produces:
  ```python
  def refresh_store_style_profile(db: Session, store_id: int) -> None
  ```
  Task 8이 답글 저장 직후 백그라운드 태스크로 이 함수를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_style_profile.py` 새로 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_style_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.style_profile'`

- [ ] **Step 3: `backend/app/llm/style_profile.py` 작성**

```python
"""매장별 답글 스타일 규칙 캐싱 — golden_examples 중 is_manual=true AND
is_synthetic=false인 데이터로만 재생성한다. 가상 데이터로 스타일을
뽑으면 AI가 자기 산출물을 학습하는 순환 오염이 생기므로 이 필터는
반드시 지킨다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.models import GoldenExample, StoreStyleProfile

_SYSTEM_PROMPT = """너는 배달 음식점 사장님의 답글 스타일을 분석한다.
아래는 이 사장님이 실제로 쓴 답글 예시들이다. 이 사장님만의 말투, 태도,
구조적 특징(예: 원인 설명 방식, 사과 표현, 재방문 유도 방식)을 5~7줄의
규칙으로 요약하라. 다른 매장에도 그대로 적용될 법한 일반적인 조언이
아니라, 이 예시들에서 실제로 관찰되는 구체적 특징만 적어라. 규칙
목록만 출력하고 다른 설명은 붙이지 마라."""


def refresh_store_style_profile(db: Session, store_id: int) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
    ).all()
    if not examples:
        return

    user_message = "\n\n".join(
        f'리뷰: "{ex.review_text}"\n답글: "{ex.reply_text}"' for ex in examples
    )
    rules = client.call_sonnet(_SYSTEM_PROMPT, user_message, max_tokens=500)

    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store_id))
    if profile is None:
        db.add(StoreStyleProfile(
            store_id=store_id, rules=rules, generated_from_count=len(examples),
            updated_at=datetime.now(timezone.utc),
        ))
    else:
        profile.rules = rules
        profile.generated_from_count = len(examples)
        profile.updated_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_style_profile.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/app/llm/style_profile.py backend/tests/test_llm_style_profile.py
git commit -m "feat: 매장 답글 스타일 프로파일 재생성(Sonnet, 진짜 예시만) 추가"
```

---

### Task 7: RAG 답글 생성 조합 함수 (Sonnet)

**Files:**
- Create: `backend/app/llm/generate.py`
- Test: `backend/tests/test_llm_generate.py` (신규 파일)

**Interfaces:**
- Consumes: Task 5의 `fetch_golden_examples`, `count_recent_same_category`;
  Task 2의 `client.call_sonnet`; `Review`, `Store`, `StoreStyleProfile` 모델
- Produces:
  ```python
  def generate_ai_reply(db: Session, review: Review, store: Store) -> str
  ```
  Task 8이 `POST /reviews/{review_id}/generate-reply`에서 이 함수를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_generate.py` 새로 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.generate'`

- [ ] **Step 3: `backend/app/llm/generate.py` 작성**

```python
"""문제 리뷰(category != "no_issue")에 대한 RAG 기반 답글 생성. 검색
(app.llm.rag)과 생성(Sonnet)을 조합한다 — 벡터 검색은 쓰지 않는다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import Review, Store, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_CATEGORY_LABELS = {
    "food_quality": "음식 품질(맛/온도/양)",
    "delivery": "배달(지연/파손)",
    "hygiene": "위생/이물질",
    "service": "응대",
    "price": "가격",
    "missing_or_wrong_item": "오배송/누락",
}


def _build_system_prompt(store: Store, style_rules: str, examples) -> str:
    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{ex.reply_text}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    return f"""너는 "{store.name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용이나 구체적 원인을 그대로 복사하지
마라. 반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""


def _build_user_message(review: Review, category_label: str, repeat_count: int) -> str:
    lines = [
        f"별점: {review.rating}",
        f"불만 유형: {category_label}",
        f'내용: "{review.content}"',
        f"이 고객의 누적 주문 횟수: {review.customer_order_count}회",
    ]
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)


def generate_ai_reply(db: Session, review: Review, store: Store) -> str:
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES

    examples = fetch_golden_examples(db, store.id, review.category, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = _CATEGORY_LABELS.get(review.category, review.category)

    system_prompt = _build_system_prompt(store, style_rules, examples)
    user_message = _build_user_message(review, category_label, repeat_count)
    return client.call_sonnet(system_prompt, user_message, max_tokens=800)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_llm_generate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/app/llm/generate.py backend/tests/test_llm_generate.py
git commit -m "feat: RAG 기반 답글 생성 조합 함수(generate_ai_reply) 추가"
```

---

### Task 8: `reviews.py` 라우터 통합 — 생성 분기 + 골든 예시 승격

**Files:**
- Modify: `backend/app/routers/reviews.py:1-16` (import), `:108-141` (`generate_reply`), `:144-167` (`save_final_reply`)
- Modify: `backend/app/llm/style_profile.py` (Task 6이 이미 만든 파일에 백그라운드 실행용 래퍼 함수 하나 추가)
- Test: `backend/tests/test_reviews.py`

**Interfaces:**
- Consumes: Task 7의 `generate_ai_reply(db, review, store) -> str`;
  Task 6의 `refresh_store_style_profile(db, store_id) -> None`
- Produces: `refresh_store_style_profile_background(store_id: int) -> None`
  (`backend/app/llm/style_profile.py`에 추가) — `POST /reviews/{id}/generate-reply`와
  `POST /reviews/{id}/reply`의 최종 외부 동작 계약을 확정한다.

> **왜 새 래퍼 함수가 필요한가**: `save_final_reply`의 `db`는 FastAPI가
> 요청 스코프로 여는 세션이다. `BackgroundTasks`에 등록된 함수는 응답이
> 전송된 *뒤에* 실행되는데, 그 시점엔 요청 스코프 세션이 이미 닫혀있을
> 수 있다 — 정확히 `backend/app/review_sync.py`의
> `run_review_sync_job(job_id)`가 `sync_reviews_for_job(job, conn, db)`를
> 감싸며 자체 `SessionLocal()`을 여는 것과 같은 이유다. 그래서
> `background_tasks.add_task(...)`에는 요청의 `db`를 그대로 넘기면 안
> 되고, 자체 세션을 여는 별도 래퍼를 넘겨야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_reviews.py` 파일 맨 아래에 추가(정확한 기존 테스트
패턴은 `test_reviews.py` 상단의 fixture들을 그대로 재사용한다 — 이미
있는 `client`/`seeded_user`/`platforms`/`auth_headers`와 리뷰 생성
헬퍼가 있으면 그걸 따른다. 없다면 아래처럼 직접 리뷰를 만든다):

```python
def test_generate_reply_uses_template_path_for_no_issue_review(client, db_session, seeded_user, platforms, auth_headers, reply_styles):
    from datetime import datetime, timezone

    from app.models import Review

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=5, content="맛있어요", customer_nickname="손님",
        category="no_issue", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    res = client.post(
        f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert res.status_code == 200
    assert "치킨" in res.json()["content"] or "손님" in res.json()["content"]  # 템플릿 치환 결과


def test_generate_reply_uses_ai_path_for_problem_review(client, db_session, seeded_user, platforms, auth_headers, reply_styles, monkeypatch):
    from datetime import datetime, timezone

    from app.models import Review
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="이물질이 나왔어요", customer_nickname="손님",
        category="hygiene", is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(reviews_mod, "generate_ai_reply", lambda db, review, store: "AI가 만든 답글입니다.")

    res = client.post(
        f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["content"] == "AI가 만든 답글입니다."


def test_save_final_reply_promotes_edited_problem_review_to_golden_example(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    from app.models import GoldenExample, Review, ReviewReply
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="배달이 늦었어요", customer_nickname="손님",
        category="delivery", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=None,
        content="AI 초안입니다.", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    calls = []
    monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: calls.append(store_id))

    res = client.post(
        f"/reviews/{review.id}/reply", json={"style_id": None, "content": "제가 직접 고친 답글입니다."}, headers=auth_headers,
    )

    assert res.status_code == 200
    example = db_session.query(GoldenExample).filter_by(source_review_id=review.id).one()
    assert example.reply_text == "제가 직접 고친 답글입니다."
    assert example.is_manual is True
    assert example.source == "organic"
    assert calls == [seeded_user["store"].id]


def test_save_final_reply_does_not_promote_when_final_matches_draft_verbatim(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    from app.models import GoldenExample, Review, ReviewReply
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="배달이 늦었어요", customer_nickname="손님",
        category="delivery", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=None,
        content="AI 초안 그대로입니다.", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: None)

    client.post(
        f"/reviews/{review.id}/reply", json={"style_id": None, "content": "AI 초안 그대로입니다."}, headers=auth_headers,
    )

    assert db_session.query(GoldenExample).filter_by(source_review_id=review.id).count() == 0


def test_save_final_reply_does_not_promote_no_issue_review(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    from app.models import GoldenExample, Review
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=5, content="맛있어요", customer_nickname="손님",
        category="no_issue", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: None)

    client.post(
        f"/reviews/{review.id}/reply", json={"style_id": None, "content": "감사합니다!"}, headers=auth_headers,
    )

    assert db_session.query(GoldenExample).count() == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py -k "ai_path or golden_example or template_path" -v`
Expected: FAIL — `reviews_mod.generate_ai_reply`/`refresh_store_style_profile`가
아직 라우터에 없어 `AttributeError`, 또는 분기가 없어 항상 템플릿
경로만 타서 `AI가 만든 답글입니다.` 어서션 실패.

- [ ] **Step 3: `backend/app/llm/style_profile.py`에 백그라운드 실행용 래퍼 추가**

Task 6이 만든 `backend/app/llm/style_profile.py` 맨 위 import에
`from app.db import SessionLocal`을 추가하고, 파일 맨 아래에 함수를
추가한다:

```python
def refresh_store_style_profile_background(store_id: int) -> None:
    """FastAPI BackgroundTasks가 호출하는 얇은 래퍼 — 요청이 끝나면 요청
    스코프 세션(app.routers.reviews의 db)은 이미 닫혀 있을 수 있으므로,
    review_sync.py의 run_review_sync_job과 같은 이유로 자체 SessionLocal을
    연다."""
    db = SessionLocal()
    try:
        refresh_store_style_profile(db, store_id)
    finally:
        db.close()
```

- [ ] **Step 4: `backend/app/routers/reviews.py` 수정**

import 섹션(현재 1-16줄) 교체:

```python
"""리뷰 관리 + 답글 스타일. 긍정 리뷰(category="no_issue")는 템플릿 기반
Mock, 문제 리뷰는 실제 Claude API 기반 RAG 생성이다."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.generate import generate_ai_reply
from app.llm.style_profile import refresh_store_style_profile_background
from app.models import GoldenExample, ReplyStyle, Review, ReviewReply, Store, Subscription, User
from app.plan import effective_plan, replies_used_today

router = APIRouter(tags=["reviews"])
```

`generate_reply` 함수(현재 108-141줄)의 템플릿 계산 부분을 분기하도록
교체 — `template = {...}` / `content = _fill_template(...)` 두 줄을
아래로 바꾼다:

```python
    if review.category == "no_issue":
        template = {"low": style.template_low, "mid": style.template_mid, "high": style.template_high}[_band(review.rating)]
        content = _fill_template(template, review, review.store)
    else:
        content = generate_ai_reply(db, review, review.store)
```

`SaveReplyRequest`(현재 144-146줄)를 아래로 교체 — RAG로 생성된 답글은
페르소나를 쓰지 않으므로 `style_id`가 없을 수 있다. 기존에는 필수
`int`였지만, 이미 DB 컬럼(`review_replies.style_id`)이 nullable이라
이 변경은 안전하다(기존 프론트가 항상 실제 style_id를 보내는 템플릿
경로는 그대로 동작한다):

```python
class SaveReplyRequest(BaseModel):
    style_id: int | None = None
    content: str
```

`save_final_reply` 함수(현재 149-167줄)를 아래로 교체(시그니처에
`background_tasks: BackgroundTasks` 추가):

```python
@router.post("/reviews/{review_id}/reply")
def save_final_reply(
    review_id: int, body: SaveReplyRequest, background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")
    if review.status == "answered":
        raise HTTPException(409, "이미 답글이 등록된 리뷰입니다")

    reply = ReviewReply(
        review_id=review.id, reply_type="final", style_id=body.style_id,
        content=body.content, created_at=datetime.now(timezone.utc),
    )
    review.status = "answered"
    db.add(reply)
    db.flush()

    if review.category != "no_issue":
        draft = db.scalar(
            select(ReviewReply)
            .where(ReviewReply.review_id == review.id, ReviewReply.reply_type == "ai_draft")
            .order_by(ReviewReply.created_at.desc())
        )
        # 초안이 아예 없이(직접 작성) 저장했거나, 초안과 다르게 고쳐서
        # 저장했으면 "진짜 사장님 목소리"로 보고 골든 예시로 승격한다.
        # 초안을 그대로 복붙했으면(AI 산출물 그대로) 승격하지 않는다.
        if draft is None or draft.content != reply.content:
            db.add(GoldenExample(
                store_id=review.store_id, category=review.category,
                review_text=review.content, reply_text=reply.content,
                is_manual=True, is_synthetic=False, source="organic",
                source_review_id=review.id, source_reply_id=reply.id,
                created_at=datetime.now(timezone.utc),
            ))
            background_tasks.add_task(refresh_store_style_profile_background, review.store_id)

    db.commit()
    return {"id": reply.id, "content": reply.content}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py -v`
Expected: PASS 전체(기존 테스트 포함 회귀 없음)

- [ ] **Step 6: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/reviews.py backend/app/llm/style_profile.py backend/tests/test_reviews.py
git commit -m "feat: 답글 생성/저장에 RAG 분기 + 골든 예시 자동 승격 연동"
```

---

### Task 9: 프론트엔드 진단 배지 + CLAUDE.md 갱신

**Files:**
- Modify: `frontend/src/app/(app)/reviews/page.tsx` (리뷰 카드 컴포넌트 — 정확한
  파일은 구현자가 `find frontend/src/app -iname "*reviews*"`로 확인한다)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `GET /reviews` 응답의 `category`/`is_sensitive` 필드(기존
  라우터가 이미 `Review` ORM 객체를 직렬화하는 방식과 동일하게 동작 —
  이 태스크에서 `list_reviews`의 직렬화 딕셔너리에 두 필드를 추가하는
  작업도 포함한다)

- [ ] **Step 1: `list_reviews` 응답에 진단 필드 추가**

`backend/app/routers/reviews.py`의 `list_reviews` 함수 안, 리뷰 응답
딕셔너리 구성부(현재 82-100줄 부근, `result.append({...})`)에서
`"status": r.status,` 다음 줄에 추가:

```python
            "status": r.status,
            "category": r.category,
            "is_sensitive": r.is_sensitive,
```

이 변경에 대한 테스트를 `backend/tests/test_reviews.py`에 추가:

```python
def test_list_reviews_includes_category_and_sensitivity(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timezone

    from app.models import Review

    db_session.add(Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="이물질이 나왔어요", customer_nickname="손님",
        category="hygiene", is_sensitive=True, created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.get("/reviews", headers=auth_headers)
    body = res.json()
    row = next(r for r in body if r["content"] == "이물질이 나왔어요")
    assert row["category"] == "hygiene"
    assert row["is_sensitive"] is True
```

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py::test_list_reviews_includes_category_and_sensitivity -v`
Expected: PASS

- [ ] **Step 2: 프론트엔드 리뷰 목록 화면 찾기**

Run: `/usr/bin/find frontend/src/app -iname "*reviews*"`

이 경로의 페이지 컴포넌트를 읽고, 리뷰 카드를 렌더링하는 위치를
확인한다.

- [ ] **Step 3: 진단 배지 추가**

리뷰 응답 타입에 `category: string`과 `is_sensitive: boolean` 필드를
추가하고, 리뷰 카드에서 `category !== "no_issue"`일 때 배지를 렌더링한다
— 정확한 카테고리 한글 라벨은 아래 매핑을 그대로 쓴다. (반복 이슈
건수는 이 태스크 범위 밖이다 — `count_recent_same_category`는 지금은
Task 7의 생성 프롬프트에만 쓰이고, 화면에 별도로 노출하는 인사이트
카드는 온보딩 플랜에서 다룬다. 재방문 여부는 이미 응답에 있는
`customer_order_count > 1`로 프론트에서 바로 판단 가능하니 새 필드가
필요 없다.)

```ts
const CATEGORY_LABELS: Record<string, string> = {
  food_quality: "음식 품질",
  delivery: "배달",
  hygiene: "위생",
  service: "응대",
  price: "가격",
  missing_or_wrong_item: "오배송/누락",
};
```

`is_sensitive`가 true면 배지를 강조 색상(`text-danger` 등 이 파일에
이미 쓰이는 기존 클래스)으로 표시한다. 기존 리뷰 카드 레이아웃/스타일은
그대로 두고 배지만 추가한다.

- [ ] **Step 4: TypeScript 컴파일 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: `CLAUDE.md` 갱신**

"### 배민 데이터 자동 동기화 스케줄러" 섹션 뒤에 새 섹션 추가(다른
절과 같은 "원래 X였으나 Y로 바꿨다" 서술 방식을 따른다):

```markdown
### LLM 기반 답글 생성 (RAG, 예외 허용)
원래 답글 생성은 `reply_styles`(4개 페르소나) 고정 템플릿에 문자열
치환만 하는 Mock이었으나(CLAUDE.md "절대 금지"의 "실제 AI API 호출
금지" 원칙), 실 SaaS 전환 로드맵 4번으로 실제 Claude API 호출을 처음
도입했다(2026-08-21 승인, 실비용 발생 인지). 리뷰가 배민에서
동기화되는 시점(`review_sync.py`)에 Haiku로 불만 유형(`category`:
food_quality/delivery/hygiene/service/price/missing_or_wrong_item/
no_issue)과 민감도(`is_sensitive`), 별점-텍스트 불일치
(`sentiment_conflict`)를 분류해 `reviews`에 저장한다 — 답글 생성
버튼을 누르기 전에도 민감 리뷰 알림(`alerts`, `sensitive_review`
타입)이 뜨게 하기 위해서다. `category="no_issue"`(불만 신호 없음)인
긍정 리뷰는 기존 4-페르소나 템플릿 경로를 그대로 쓰고, 그 외 문제
리뷰만 새 RAG 경로(`backend/app/llm/`)를 탄다 — 이 가게의 진짜 답글
사례(`golden_examples`, `category` 필터로만 검색하고 벡터 DB는 쓰지
않는다)와 매장별 스타일 규칙(`store_style_profile`, Sonnet이 5~7줄로
요약해 캐싱)을 few-shot으로 반영해 Sonnet이 생성한다. few-shot
프롬프트에는 "스타일만 참고, 사건 내용 복사 금지" 지시를 반드시
포함한다(소량 예시의 과적합 방지). 사장님이 AI 초안을 수정하거나
초안 없이 직접 써서 저장하면 그 답글이 자동으로 새 골든 예시로
승격되고(`is_manual=true`), 스타일 프로파일이 재생성된다 — 단
`store_style_profile` 재생성은 반드시 `is_manual=true AND
is_synthetic=false` 데이터로만 하며, 순수 AI 생성 모범답안을 학습
소스로 쓰지 않는다(자기 산출물을 자기가 학습하는 순환 오염 방지).
브레인스토밍 중 프로덕션 데이터를 실측 확인해, 기존 답글 700여 건은
사장님이 실제 사용 중인 별도 AI 도구 + 직접 작성 결과였고(seed Mock
아님), 그중 별점 1~2점 답글 5건은 전부 사장님이 직접 썼다고 확인받아
`backend/scripts/backfill_golden_examples.py`로 골든 예시에 백필했다.
데이터가 적을 때 AI로 예시를 증강하는 방식("메아리 증폭" — 편향만
증폭되고 정보량은 그대로)은 명시적으로 채택하지 않았다 — 대신 사장님
온보딩으로 진짜 예시를 늘리는 별도 계획
(`docs/superpowers/specs/2026-08-21-llm-rag-reply-design.md`의
온보딩 절)을 이어서 진행한다. `ANTHROPIC_API_KEY`는 Claude Pro 구독과
무관한 별도 과금 API 키다(console.anthropic.com 발급). 설계 상세는
`docs/superpowers/specs/2026-08-21-llm-rag-reply-design.md` 참고.
```

또한 "### 테이블 용도" 아래 `reviews`, `review_replies` 설명 사이(또는
`alerts` 항목 근처)에 새 테이블 3개 설명을 추가:

```
- golden_examples: RAG few-shot 소스. 사장님이 직접 쓰거나 승인한 진짜
  답글(is_manual=true)과 예시 부족 시 보충하는 순수 AI 생성 모범답안
  (is_synthetic=true)을 함께 담는다. 검색은 category 필터만 쓴다(벡터
  DB 미사용).
- store_style_profile: 매장별 답글 스타일 규칙(5~7줄) 캐싱. 진짜
  골든 예시로만 재생성한다.
```

`review_replies` 설명 근처에는 변경하지 않는다(스키마 변경 없음).

`alerts` 설명("부정 리뷰, 미답변, 순위 하락 알림")을 아래로 교체:

```
- alerts: 부정 리뷰, 미답변, 순위 하락, 민감 리뷰(sensitive_review) 알림.
  민감 리뷰는 실제로 동적 생성되고(review_sync.py), 나머지 세 타입은
  여전히 seed.sql Mock이다(실동작화는 별도 스코프).
```

- [ ] **Step 6: 전체 백엔드 테스트 스위트 최종 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/reviews.py backend/tests/test_reviews.py "frontend/src/app/(app)/reviews/page.tsx" CLAUDE.md
git commit -m "feat: 리뷰 목록에 불만 유형/민감도 배지 표시 + CLAUDE.md 문서화"
```

(프론트엔드 파일 경로는 Step 2에서 확인한 실제 경로로 바꿔서 커밋한다.)

---

## 최종 확인 (플랜 조율 에이전트가 직접 수행 — 서브에이전트 위임 대상 아님)

1. `cd backend && .venv/bin/pytest -q` 전체 통과 확인
2. Task 1의 배포 노트에 적힌 DDL을 실제 Railway Postgres에 실행
3. Railway 백엔드 환경변수에 `ANTHROPIC_API_KEY` 추가
4. Railway 백엔드 배포
5. Task 4의 백필 스크립트를 실제 데모 매장 store_id로 1회 실행
   (`ANTHROPIC_API_KEY` 필요 — 실제 비용 발생, 소량이라 무시 가능한
   수준)
6. 실제 부정 리뷰(또는 별점-텍스트 불일치가 있는 리뷰)에 "답글 생성"을
   눌러 RAG 경로가 실제로 다른 결과를 내는지, 긍정 리뷰는 기존 템플릿
   그대로인지 실측 확인
7. 민감 리뷰가 실제로 동기화되면 `alerts`에 `sensitive_review` 행이
   생기는지 확인(다음 데이터 동기화 실행 시)
