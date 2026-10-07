# DeepTwin 원칙 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `store_style_profile`을 매장당 1행에서 (매장, 불만 카테고리)당 1행으로 세분화해 "배달지연엔 이렇게, 맛 불만엔 저렇게" 같은 상황별 판단 원칙을 따로 증류하고, AI가 뽑아낸 원칙 요약이 실제로 바뀔 때마다 사장님이 확인/수정할 수 있는 비차단 UI를 추가한다.

**Architecture:** 기존 `refresh_store_style_profile`(golden_examples → Sonnet 요약 → 캐시)의 메커니즘 자체는 그대로 두고 `category` 차원만 추가한다 — 매장×카테고리별로 독립된 캐시 행이 되고, 재생성 때마다 이전 텍스트와 비교해 실제로 바뀌었을 때만 `needs_confirmation` 플래그를 세운다. LangGraph 루프(`retrieve_memory_node`)는 리뷰의 카테고리에 맞는 원칙만 조회해 쓰도록 조회 키를 바꾼다. 새 경량 라우터가 `needs_confirmation=true`인 행만 노출하고, 훈련카드 옆에 비슷한 모양의 새 카드가 "AI가 파악한 OO 대응 원칙"을 보여주고 확인/수정을 받는다.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Next.js(기존 `reviews/styles` 페이지 확장).

## Global Constraints

- **재생성 메커니즘 자체는 바꾸지 않는다** — golden_examples(is_manual=true AND is_synthetic=false AND needs_confirmation=false)로만 Sonnet을 호출해 5~7줄 규칙을 뽑는 방식은 그대로다. 이번에 추가하는 건 `category` 필터와 `needs_confirmation`(원칙 확인용, golden_examples의 동명 컬럼과는 다른 테이블의 별개 플래그) 하나뿐이다.
- **원칙 확인은 비정기적·비차단이다** — 그 카테고리의 원칙이 **실제로 바뀔 때만**(이전 `rules` 텍스트와 다를 때만) `needs_confirmation=true`가 선다. 새 원칙은 사장님 확인 여부와 무관하게 **즉시 자동 적용**된다(`retrieve_memory_node`가 그 행을 바로 읽는다) — 확인 화면은 "여유 있을 때 검토, 틀리면 수정"하는 선택 사항이고 답글 생성을 막지 않는다.
- **훈련카드(OnboardingScenario)와 원칙 확인은 별개 장치**다 — 훈련카드는 판단사례(원재료)를 새로 만드는 장치, 원칙 확인은 이미 쌓인 사례에서 AI가 뽑아낸 요약이 맞는지 검수하는 장치. 데이터 흐름상 훈련카드(사례 수집) → 원칙 확인(그 사례의 요약 검증) 순서로 이어지는 보완 관계라 같은 화면(`reviews/styles`)에 나란히 둔다.
- **기존 `store_style_profile` 데이터는 이번 마이그레이션으로 버린다** — 이 테이블은 golden_examples에서 언제든 다시 뽑아낼 수 있는 캐시이고(기존 CLAUDE.md 설명), 바뀐 스키마(카테고리별)에서는 기존 "매장 전체 통합" 행이 어떤 카테고리에도 정확히 대응하지 않는다. 다음 저장 시점에 해당 카테고리 행이 자연히 다시 생긴다 — 데이터 유실이 아니라 콜드스타트다. **정정(2026-10-07 최종 리뷰)**: 이 테이블 자체는 Alembic 이전 시절(2026-08-21)에 schema.sql로 이미 운영에 만들어져 실 데이터가 쌓여 있다 — "마이그레이션 0001이 아직 안 적용됐다"는 별개 사실과 혼동하면 안 된다. 버려도 안전한 진짜 이유는 캐시라는 것뿐이고, 배포 직후 `backend/scripts/backfill_store_style_profiles.py`로 golden_examples에서 한 번에 다시 채워야 콜드스타트 기간이 생기지 않는다.
- **새 엔드포인트도 기존 ownership 체크 패턴을 그대로 따른다** — `reply_settings.py`의 `store = db.get(Store, sid); if store is None or store.user_id != user.id: raise HTTPException(404, ...)` 패턴을 그대로 쓴다.

---

### Task 1: `store_style_profile`을 (store_id, category) 복합키로 재설계

**Files:**
- Modify: `backend/app/models.py`(`StoreStyleProfile` 클래스)
- Create: `backend/alembic/versions/0004_store_style_profile_per_category.py`
- Modify: `schema.sql`
- Create: `backend/tests/test_models_store_style_profile.py`

**Interfaces:**
- Consumes: 없음(독립적인 스키마 변경).
- Produces: `StoreStyleProfile(store_id, category, rules, generated_from_count, needs_confirmation, updated_at)` — PK는 `(store_id, category)`. 이후 모든 태스크가 이 컬럼들에 쓰거나 읽는다.

- [ ] **Step 1: `models.py`의 `StoreStyleProfile` 재정의**

`backend/app/models.py`에서 현재:

```python
class StoreStyleProfile(Base):
    __tablename__ = "store_style_profile"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    rules: Mapped[str] = mapped_column(Text)
    generated_from_count: Mapped[int]
    updated_at: Mapped[datetime]
```

를 다음으로 교체:

```python
class StoreStyleProfile(Base):
    """매장×카테고리별 답글 스타일 원칙 캐싱(2026-10-07부터 카테고리별로
    분리 — 스펙 4.1절 "원칙 레이어 고도화"). golden_examples 중
    is_manual=true AND is_synthetic=false AND needs_confirmation=false인
    데이터로만, 그 카테고리 안에서만 재생성한다(app/llm/style_profile.py).

    needs_confirmation(이 테이블 고유 플래그 — golden_examples의 동명
    컬럼과는 다른 의미)은 재생성 결과가 이전 rules 텍스트와 실제로
    다를 때만 선다. 사장님이 확인/수정하면 꺼진다 — 새 원칙은 이 플래그와
    무관하게 이미 적용 중이라, 이건 순수하게 "검수했는지" 표시다."""
    __tablename__ = "store_style_profile"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), primary_key=True)
    rules: Mapped[str] = mapped_column(Text)
    generated_from_count: Mapped[int]
    needs_confirmation: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime]
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_models_store_style_profile.py` 새로 작성:

```python
from datetime import datetime, timezone

from app.models import StoreStyleProfile


def test_store_style_profile_composite_key_allows_multiple_categories_per_store(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add_all([
        StoreStyleProfile(
            store_id=sid, category="delivery", rules="배달 원칙", generated_from_count=2,
            needs_confirmation=True, updated_at=datetime.now(timezone.utc),
        ),
        StoreStyleProfile(
            store_id=sid, category="food_quality", rules="맛 원칙", generated_from_count=3,
            needs_confirmation=False, updated_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    rows = db_session.query(StoreStyleProfile).filter_by(store_id=sid).order_by(StoreStyleProfile.category).all()
    assert [r.category for r in rows] == ["delivery", "food_quality"]
    assert rows[0].rules == "배달 원칙"
    assert rows[0].needs_confirmation is True
    assert rows[1].needs_confirmation is False


def test_store_style_profile_lookup_by_composite_key(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="위생 원칙", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fetched = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert fetched is not None
    assert fetched.rules == "위생 원칙"
    assert db_session.get(StoreStyleProfile, (sid, "service")) is None
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_models_store_style_profile.py -v`
Expected: FAIL — `TypeError: 'category' is an invalid keyword argument for StoreStyleProfile`(컬럼이 아직 없음)

- [ ] **Step 4: Alembic 마이그레이션 작성**

`backend/alembic/versions/0004_store_style_profile_per_category.py` 새로 작성:

```python
"""store style profile per category

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

DeepTwin 원칙 UI 플랜(docs/superpowers/plans/
2026-10-07-deeptwin-principle-ui.md)의 스키마 변경. store_style_profile을
매장당 1행에서 (store_id, category)당 1행으로 재설계한다 — 스펙 4.1절
"원칙 레이어 고도화"(카테고리별 판단 원칙 증류).

기존 행은 전부 버린다 — store_style_profile은 golden_examples에서 언제든
다시 뽑아낼 수 있는 캐시이고, 바뀐 스키마에서는 기존 "매장 전체 통합" 행이
어떤 카테고리에도 정확히 대응하지 않는다. 다음 저장(save_final_reply/
answer_scenario) 시점에 해당 카테고리 행이 자연히 다시 생긴다 — 데이터
유실이 아니라 콜드스타트다. 정정(2026-10-07 최종 리뷰): 이 테이블
자체는 Alembic 이전 시절(2026-08-21)에 schema.sql로 이미 운영에
만들어져 실 데이터가 있다 — 버려도 안전한 진짜 이유는 캐시라는
것뿐이다. 배포 직후 backend/scripts/backfill_store_style_profiles.py로
한 번에 다시 채운다(Global Constraints 참고).
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE store_style_profile")
    op.execute("""
        CREATE TABLE store_style_profile (
            store_id             BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            category             VARCHAR(24)  NOT NULL,
            rules                TEXT         NOT NULL,
            generated_from_count INT          NOT NULL,
            needs_confirmation   BOOLEAN      NOT NULL DEFAULT false,
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (store_id, category)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE store_style_profile")
    op.execute("""
        CREATE TABLE store_style_profile (
            store_id             BIGINT       PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
            rules                TEXT         NOT NULL,
            generated_from_count INT          NOT NULL,
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
```

- [ ] **Step 5: `schema.sql` 손으로 동기화**

`schema.sql`의 `store_style_profile` `CREATE TABLE` 블록(16-2번, 주석
"매장별 답글 스타일 규칙 캐싱" 바로 아래)을 다음으로 교체:

```sql
-- ----------------------------------------------------------------------------
-- 16-2. store_style_profile — 매장×카테고리별 답글 스타일 원칙 캐싱
--       (2026-10-07부터 카테고리별 분리 — DeepTwin 원칙 UI 플랜). golden_examples
--       중 is_manual=true AND is_synthetic=false AND needs_confirmation=false인
--       데이터로, 그 카테고리 안에서만 재생성한다(가상 데이터로 스타일을
--       뽑으면 AI가 자기 산출물을 학습하는 순환 오염이 생긴다). 이 테이블의
--       needs_confirmation은 golden_examples의 동명 컬럼과 다른 의미다 —
--       재생성 결과가 이전 rules와 실제로 다를 때만 선다("원칙 확인" UI
--       대상 표시일 뿐, 새 원칙 자체는 이 플래그와 무관하게 이미 적용 중).
-- ----------------------------------------------------------------------------
CREATE TABLE store_style_profile (
    store_id             BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category             VARCHAR(24)  NOT NULL,
    rules                TEXT         NOT NULL,
    generated_from_count INT          NOT NULL,
    needs_confirmation   BOOLEAN      NOT NULL DEFAULT false,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, category)
);
```

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_models_store_style_profile.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

이 시점에 `backend/tests/test_llm_style_profile.py`의 기존 4개 테스트가
깨져 있을 것이다(`StoreStyleProfile(store_id=sid, rules=..., ...)`를
`category` 없이 생성하는 자리가 있다) — **이건 정상이다, Task 2에서
고친다.** 지금은 아래만 확인하고 커밋해라:

Run: `cd backend && pytest tests/test_models_store_style_profile.py -v`
Expected: PASS (방금 만든 새 테스트 2개만 — 전체 스위트는 아직 돌리지 마라)

```bash
git add backend/app/models.py backend/alembic/versions/0004_store_style_profile_per_category.py schema.sql backend/tests/test_models_store_style_profile.py
git commit -m "feat: store_style_profile을 (store_id, category) 복합키로 재설계"
```

---

### Task 2: `refresh_store_style_profile`이 카테고리별로 재생성 + `needs_confirmation` 플래그

**Files:**
- Modify: `backend/app/llm/style_profile.py`
- Modify: `backend/app/llm/agent_graph.py:87-115`(`retrieve_memory_node`)
- Modify: `backend/app/routers/reviews.py:194`
- Modify: `backend/app/routers/reply_onboarding.py:139`
- Modify: `backend/tests/test_llm_style_profile.py`(전체 재작성)
- Modify: `backend/tests/test_reviews.py`, `backend/tests/test_reply_onboarding.py`(모든 `refresh_store_style_profile_background` 몽키패치 시그니처 수정)

**Interfaces:**
- Consumes: Task 1의 `StoreStyleProfile.category`/`needs_confirmation` 컬럼.
- Produces: `refresh_store_style_profile(db, store_id, category) -> None`,
  `refresh_store_style_profile_background(store_id, category) -> None` — 둘 다
  `category`가 새 필수 위치 인자다(기존엔 2개/1개 인자였음). Task 3이
  `needs_confirmation=true`인 행을 읽는다.

- [ ] **Step 1: `style_profile.py` 재작성**

`backend/app/llm/style_profile.py` 전체를 다음으로 교체:

```python
"""매장×카테고리별 답글 스타일 원칙 캐싱 — golden_examples 중
is_manual=true AND is_synthetic=false AND needs_confirmation=false인
데이터로만, 그 카테고리 안에서만 재생성한다. 가상 데이터로 스타일을
뽑으면 AI가 자기 산출물을 학습하는 순환 오염이 생기므로 이 필터는
반드시 지킨다. needs_confirmation=true 행(경로 C — 배민에 직접 단
답글이 기존 신뢰 예시 클러스터와 말투가 어긋나는 이상치로 판정된 것,
app/llm/rag.py의 promote_direct_reply_to_golden_example 참고)도 같은
이유로 제외한다 — 아직 사람이 확인하지 않은, 진짜 사장님 말투인지
의심되는 답글을 "이 사장님의 말투"를 정의하는 요약에 그대로 반영하면
안 된다(2026-10-06).

카테고리별 분리(2026-10-07, DeepTwin 원칙 UI 플랜, 스펙 4.1절)와 함께
StoreStyleProfile.needs_confirmation(이 테이블 고유 플래그) 로직도
여기서 관리한다 — 재생성 결과가 이전 rules 텍스트와 실제로 다를 때만
선다. "원칙이 실제로 바뀔 때만 뜬다"는 스펙 요구사항을 만족하려면 매
저장마다 무조건 플래그를 세우면 안 되고, 텍스트가 우연히 똑같이
재생성된 경우(또는 사장님이 이미 확인한 뒤 또 같은 텍스트가 나온 경우)는
플래그를 건드리지 않아야 한다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.llm import client
from app.models import GoldenExample, StoreStyleProfile

_SYSTEM_PROMPT = """너는 배달 음식점 사장님의 답글 스타일을 분석한다.
아래는 이 사장님이 실제로 쓴 답글 예시들이다. 이 사장님만의 말투, 태도,
구조적 특징(예: 원인 설명 방식, 사과 표현, 재방문 유도 방식)을 5~7줄의
규칙으로 요약하라. 다른 매장에도 그대로 적용될 법한 일반적인 조언이
아니라, 이 예시들에서 실제로 관찰되는 구체적 특징만 적어라. 규칙
목록만 출력하고 다른 설명은 붙이지 마라."""


def refresh_store_style_profile(db: Session, store_id: int, category: str) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
            GoldenExample.needs_confirmation.is_(False),
        )
    ).all()
    if not examples:
        return

    user_message = "\n\n".join(
        f'리뷰: "{ex.review_text}"\n답글: "{ex.reply_text}"' for ex in examples
    )
    rules = client.call_sonnet(_SYSTEM_PROMPT, user_message, max_tokens=500)

    profile = db.get(StoreStyleProfile, (store_id, category))
    changed = profile is None or profile.rules != rules
    if profile is None:
        db.add(StoreStyleProfile(
            store_id=store_id, category=category, rules=rules, generated_from_count=len(examples),
            needs_confirmation=changed, updated_at=datetime.now(timezone.utc),
        ))
    else:
        profile.rules = rules
        profile.generated_from_count = len(examples)
        profile.updated_at = datetime.now(timezone.utc)
        if changed:
            profile.needs_confirmation = True
    db.commit()


def refresh_store_style_profile_background(store_id: int, category: str) -> None:
    """FastAPI BackgroundTasks가 호출하는 얇은 래퍼 — 요청이 끝나면 요청
    스코프 세션(app.routers.reviews의 db)은 이미 닫혀 있을 수 있으므로,
    review_sync.py의 run_review_sync_job과 같은 이유로 자체 SessionLocal을
    연다."""
    db = SessionLocal()
    try:
        refresh_store_style_profile(db, store_id, category)
    finally:
        db.close()
```

- [ ] **Step 2: 호출부 2곳 수정**

`backend/app/routers/reviews.py:194`의

```python
        background_tasks.add_task(refresh_store_style_profile_background, review.store_id)
```

를

```python
        background_tasks.add_task(refresh_store_style_profile_background, review.store_id, review.category)
```

로 바꾼다.

`backend/app/routers/reply_onboarding.py:139`의

```python
    background_tasks.add_task(refresh_store_style_profile_background, scenario.store_id)
```

를

```python
    background_tasks.add_task(refresh_store_style_profile_background, scenario.store_id, scenario.category)
```

로 바꾼다.

- [ ] **Step 3: `retrieve_memory_node`가 카테고리별로 원칙을 조회하도록 수정**

`backend/app/llm/agent_graph.py:93`의

```python
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
```

를

```python
    profile = db.get(StoreStyleProfile, (store.id, review.category))
```

로 바꾼다(바로 다음 줄 `style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES`는 그대로 둔다 — 카테고리에 맞는 원칙이 아직 없으면 기존처럼 폴백한다).

- [ ] **Step 4: 기존 테스트 파일들에 흩어진 시그니처 불일치를 전부 고친다**

**4-a. `backend/tests/test_llm_style_profile.py` 전체 재작성** — 함수
시그니처가 `(db, store_id)` → `(db, store_id, category)`로 바뀌어서 기존
4개 테스트가 전부 깨진다. 전체 파일을 다음으로 교체(기존 4개 테스트를
새 시그니처에 맞게 고치고, 카테고리 격리/needs_confirmation 동작을
검증하는 테스트 4개를 추가한다):

```python
from datetime import datetime, timezone

from app.llm import style_profile
from app.models import GoldenExample, StoreStyleProfile


def _make_example(db_session, store_id, *, is_manual, is_synthetic, category="hygiene"):
    ex = GoldenExample(
        store_id=store_id, category=category, review_text="이물질이 나왔어요",
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

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert "구체적 원인" in profile.rules
    assert profile.generated_from_count == 1  # is_synthetic 예시는 제외
    assert "이물질이 나왔어요" in captured["user"]


def test_refresh_excludes_needs_confirmation_examples(db_session, seeded_user, monkeypatch):
    """needs_confirmation=True(경로 C — 배민에 직접 단 답글이 기존 신뢰
    예시 클러스터와 말투가 어긋나는 이상치로 판정된 것)인 예시는
    is_manual=true/is_synthetic=false를 만족해도 스타일 요약 입력에서
    빠져야 한다 — 아직 사람이 확인하지 않은, 진짜 사장님 말투인지 의심되는
    답글이 "이 사장님의 말투" 요약에 섞이면 안 된다."""
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    unconfirmed = GoldenExample(
        store_id=sid, category="hygiene", review_text="의심스러운 리뷰 본문",
        reply_text="의심스러운 답글 본문",
        is_manual=True, is_synthetic=False, source="organic_direct",
        needs_confirmation=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(unconfirmed)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "- 구체적 원인을 설명한다"

    monkeypatch.setattr(style_profile.client, "call_sonnet", _fake_call_sonnet)

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.generated_from_count == 1  # needs_confirmation 예시는 제외
    assert "의심스러운 리뷰 본문" not in captured["user"]
    assert "이물질이 나왔어요" in captured["user"]  # confirmed 예시는 그대로 반영


def test_refresh_updates_existing_profile(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="옛날 규칙", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()

    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "새 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.rules == "새 규칙"


def test_refresh_noop_when_no_manual_examples(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    calls = []
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: calls.append(1) or "무시됨")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    assert calls == []  # 예시가 없으면 API 호출 자체를 안 함
    assert db_session.get(StoreStyleProfile, (sid, "hygiene")) is None


def test_refresh_only_includes_matching_category_examples(db_session, seeded_user, monkeypatch):
    """다른 카테고리(delivery)에 예시가 쌓여도 hygiene 카테고리를
    재생성할 때는 섞여 들어가면 안 된다 — 카테고리별로 완전히 독립된
    캐시 행이어야 한다."""
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False, category="hygiene")
    other = GoldenExample(
        store_id=sid, category="delivery", review_text="배달이 너무 늦었어요",
        reply_text="배달 지연으로 불편을 드려 죄송합니다",
        is_manual=True, is_synthetic=False, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        style_profile.client, "call_sonnet",
        lambda system, user, **kw: captured.setdefault("user", user) and "- 요약",
    )

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    assert "이물질" in captured["user"]
    assert "배달" not in captured["user"]
    assert db_session.get(StoreStyleProfile, (sid, "delivery")) is None  # delivery는 아직 재생성 안 함


def test_refresh_sets_needs_confirmation_when_profile_is_new(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "새 원칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True


def test_refresh_sets_needs_confirmation_when_rules_text_changes(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="옛날 규칙", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "완전히 다른 새 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True


def test_refresh_does_not_reset_needs_confirmation_when_rules_text_unchanged(db_session, seeded_user, monkeypatch):
    """이미 확인 완료(needs_confirmation=False)인 원칙이, 다시 돌려도
    똑같은 텍스트로 재생성되면 — 사장님이 또 확인할 필요가 없으므로 —
    플래그를 다시 세우면 안 된다("원칙이 실제로 바뀔 때만 뜬다")."""
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="변하지 않는 규칙", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "변하지 않는 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is False
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_llm_style_profile.py -v`
Expected: PASS (8개 전부)

**4-b. `backend/tests/test_reviews.py`와 `backend/tests/test_reply_onboarding.py`의
`refresh_store_style_profile_background` 몽키패치 13곳 전부 시그니처
수정** — 두 파일에서 다음 명령으로 모든 자리를 찾아라:

```bash
grep -n '"refresh_store_style_profile_background", lambda store_id' backend/tests/test_reviews.py backend/tests/test_reply_onboarding.py
```

13곳이 나와야 한다(`test_reviews.py` 7곳, `test_reply_onboarding.py`
6곳). **이 13곳 전부**, `lambda store_id:`로 시작하는 부분을
`lambda store_id, category:`로만 바꿔라 — 람다 본문(`None`을 돌려주는지,
`calls.append(store_id)`/`refreshed.append(store_id)`처럼 리스트에
쌓는지)은 전혀 건드리지 않는다. 예를 들어:

```python
monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: None)
```

은

```python
monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id, category: None)
```

이 되고,

```python
monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id: calls.append(store_id))
```

은

```python
monkeypatch.setattr(reviews_mod, "refresh_store_style_profile_background", lambda store_id, category: calls.append(store_id))
```

이 된다(여기서도 `calls.append(store_id)`는 그대로 — `category`는 그냥
받기만 하고 안 쓴다). 13곳을 다 고친 뒤 다시 같은 grep 명령을 돌려서
`lambda store_id:`(콤마 없이 끝나는 패턴)로 남은 게 없는지 확인해라.

- [ ] **Step 6: 전체 테스트 스위트 돌려서 회귀 없는지 확인**

Run: `cd backend && pytest`
Expected: 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/llm/style_profile.py backend/app/llm/agent_graph.py backend/app/routers/reviews.py backend/app/routers/reply_onboarding.py backend/tests/test_llm_style_profile.py backend/tests/test_reviews.py backend/tests/test_reply_onboarding.py
git commit -m "feat: 답글 스타일 원칙을 카테고리별로 재생성하고 변경 시 needs_confirmation을 세운다"
```

---

### Task 3: 원칙 확인 API — `GET /style-principles`, `POST /style-principles/{category}/confirm`

**Files:**
- Create: `backend/app/routers/style_principles.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_style_principles.py`

**Interfaces:**
- Consumes: Task 1/2의 `StoreStyleProfile(store_id, category, rules, generated_from_count, needs_confirmation, updated_at)`.
- Produces: `GET /style-principles?store_id=` → `{"principles": [{"category", "label", "rules", "generated_from_count", "updated_at"}, ...]}`
  (needs_confirmation=true인 것만). `POST /style-principles/{category}/confirm?store_id=`
  body `{"rules": str}` → 그 행을 갱신하고 needs_confirmation을 끈다.
  Task 4(프론트엔드)가 이 두 엔드포인트를 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_style_principles.py` 새로 작성:

```python
from datetime import datetime, timezone

from app.models import StoreStyleProfile


def test_list_principles_returns_only_unconfirmed(client, auth_headers, db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add_all([
        StoreStyleProfile(
            store_id=sid, category="delivery", rules="배달 원칙", generated_from_count=3,
            needs_confirmation=True, updated_at=datetime.now(timezone.utc),
        ),
        StoreStyleProfile(
            store_id=sid, category="hygiene", rules="위생 원칙", generated_from_count=2,
            needs_confirmation=False, updated_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    resp = client.get(f"/style-principles?store_id={sid}", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["principles"]) == 1
    assert body["principles"][0]["category"] == "delivery"
    assert body["principles"][0]["rules"] == "배달 원칙"
    assert body["principles"][0]["label"] == "배달(지연/파손)"


def test_list_principles_empty_when_nothing_needs_confirmation(client, auth_headers, seeded_user):
    sid = seeded_user["store"].id
    resp = client.get(f"/style-principles?store_id={sid}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"principles": []}


def test_confirm_principle_clears_flag_and_updates_rules(client, auth_headers, db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="delivery", rules="AI가 뽑은 원칙", generated_from_count=3,
        needs_confirmation=True, updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    resp = client.post(
        f"/style-principles/delivery/confirm?store_id={sid}",
        json={"rules": "사장님이 고친 원칙"}, headers=auth_headers,
    )

    assert resp.status_code == 200
    profile = db_session.get(StoreStyleProfile, (sid, "delivery"))
    assert profile.rules == "사장님이 고친 원칙"
    assert profile.needs_confirmation is False


def test_confirm_principle_404_when_no_such_category(client, auth_headers, seeded_user):
    sid = seeded_user["store"].id
    resp = client.post(
        f"/style-principles/delivery/confirm?store_id={sid}",
        json={"rules": "아무거나"}, headers=auth_headers,
    )

    assert resp.status_code == 404


def test_list_principles_404_for_other_users_store(client, auth_headers, db_session, seeded_user):
    from app.models import Store, User

    other_user = User(email="other@example.com", nickname="다른사장", created_at=datetime.now(timezone.utc))
    db_session.add(other_user)
    db_session.flush()
    other_store = Store(
        user_id=other_user.id, name="다른 가게", category="한식",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_store)
    db_session.commit()

    resp = client.get(f"/style-principles?store_id={other_store.id}", headers=auth_headers)

    assert resp.status_code == 404
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && pytest tests/test_style_principles.py -v`
Expected: FAIL — `404 Not Found`(라우터가 아직 없음)

- [ ] **Step 3: `style_principles.py` 작성**

`backend/app/routers/style_principles.py` 새로 작성:

```python
"""매장×카테고리별 답글 스타일 원칙("원칙 확인") — AI가 golden_examples에서
뽑아낸 요약(StoreStyleProfile)이 실제로 바뀔 때마다 사장님이 확인/수정할
수 있는 비차단 UI의 API. 훈련카드(reply_onboarding.py)와는 별개 장치다 —
훈련카드는 판단사례를 새로 만들고, 여기는 이미 쌓인 사례의 요약을
검수한다. 새 원칙은 이 확인 여부와 무관하게 이미 적용 중이다
(app/llm/agent_graph.py의 retrieve_memory_node가 needs_confirmation을
보지 않고 그냥 읽는다) — 여기서 하는 일은 "검수했는지" 표시뿐이다."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.generate import CATEGORY_LABELS
from app.models import Store, StoreStyleProfile, User

router = APIRouter(tags=["style-principles"])

_NO_ISSUE_LABEL = "특이 불만 없음"


def _label(category: str) -> str:
    return {**CATEGORY_LABELS, "no_issue": _NO_ISSUE_LABEL}.get(category, category)


@router.get("/style-principles")
def list_principles(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    rows = db.scalars(
        select(StoreStyleProfile)
        .where(StoreStyleProfile.store_id == sid, StoreStyleProfile.needs_confirmation.is_(True))
        .order_by(StoreStyleProfile.updated_at)
    ).all()
    return {
        "principles": [
            {
                "category": p.category, "label": _label(p.category), "rules": p.rules,
                "generated_from_count": p.generated_from_count, "updated_at": p.updated_at.isoformat(),
            }
            for p in rows
        ],
    }


class ConfirmPrincipleRequest(BaseModel):
    rules: str


@router.post("/style-principles/{category}/confirm")
def confirm_principle(
    category: str, body: ConfirmPrincipleRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    profile = db.get(StoreStyleProfile, (sid, category))
    if profile is None:
        raise HTTPException(404, "해당 카테고리의 원칙이 없습니다")

    profile.rules = body.rules
    profile.needs_confirmation = False
    db.commit()
    return {
        "category": profile.category, "label": _label(profile.category), "rules": profile.rules,
        "generated_from_count": profile.generated_from_count, "updated_at": profile.updated_at.isoformat(),
    }
```

- [ ] **Step 4: `main.py`에 라우터 등록**

`backend/app/main.py:8`의

```python
from app.routers import admin, ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections
```

를

```python
from app.routers import admin, ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections, style_principles
```

로 바꾸고, `app.include_router(reply_onboarding.router)` 다음 줄에 추가:

```python
app.include_router(style_principles.router)
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `cd backend && pytest tests/test_style_principles.py -v`
Expected: PASS (5개 전부)

- [ ] **Step 6: 전체 테스트 스위트 회귀 확인**

Run: `cd backend && pytest`
Expected: 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/style_principles.py backend/app/main.py backend/tests/test_style_principles.py
git commit -m "feat: 원칙 확인 API(GET /style-principles, POST .../confirm) 추가"
```

---

### Task 4: 프론트엔드 — "AI 원칙 확인" 카드

**Files:**
- Modify: `frontend/src/app/(app)/reviews/styles/page.tsx`

**Interfaces:**
- Consumes: Task 3의 `GET /style-principles`, `POST /style-principles/{category}/confirm`.
- Produces: 없음(이 플랜의 마지막 태스크 — UI 소비자가 끝).

- [ ] **Step 1: 타입 + 컴포넌트 추가**

`frontend/src/app/(app)/reviews/styles/page.tsx`의 `OnboardingScenario`
타입 선언(`:18-24`) 바로 뒤에 추가:

```tsx
type StylePrinciple = {
  category: string;
  label: string;
  rules: string;
  generated_from_count: number;
  updated_at: string;
};
```

`OnboardingTrainingCard` 함수(`:35-133`) 바로 뒤, `export default function
ReplyStylesPage()` 앞에 새 컴포넌트 추가:

```tsx
function PrincipleReviewCard({ storeId }: { storeId: number }) {
  const [principles, setPrinciples] = useState<StylePrinciple[] | null>(null);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    apiGet<{ principles: StylePrinciple[] }>(`/style-principles?store_id=${storeId}`)
      .then((r) => setPrinciples(r.principles))
      .catch(() => setPrinciples([]));
  }, [storeId]);

  const current = principles?.[index] ?? null;

  useEffect(() => {
    if (current) setDraft(current.rules);
  }, [current]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 24}px`;
  }, [draft]);

  if (!principles || principles.length === 0 || !current) return null;

  const advance = () => {
    if (index + 1 < principles.length) {
      setIndex(index + 1);
    } else {
      setPrinciples([]);
    }
  };

  const confirm = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiPost(`/style-principles/${current.category}/confirm?store_id=${storeId}`, { rules: draft });
      advance();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "확인에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={`AI 원칙 확인 (${index + 1}/${principles.length})`}>
      <p className="mb-3 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        &quot;{current.label}&quot; 유형 답글에서 AI가 파악한 사장님 말투 원칙이 바뀌었어요
        (실제 답글 {current.generated_from_count}건 기준). 맞는지 확인하거나, 틀린 부분이 있으면
        고쳐서 저장해주세요 — 새 원칙은 이미 답글 생성에 적용되고 있어요.
      </p>
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={saving}
        className="w-full resize-none overflow-hidden rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
      />
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button
          onClick={confirm}
          disabled={saving || !draft.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          확인
        </button>
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: `ReplyStylesPage`에서 렌더링**

`ReplyStylesPage` 함수 안, `{storeId && <OnboardingTrainingCard
storeId={storeId} />}` 줄(`:176`) 바로 뒤에 추가:

```tsx
      {storeId && <PrincipleReviewCard storeId={storeId} />}
```

(훈련카드 다음, 아래 "답글 스타일 설정" 제목 전에 위치하게 된다 — 스펙의
"훈련카드(사례 수집) → 원칙 확인(그 사례의 요약 검증)" 순서와 같은
순서로 화면에 나타난다.)

- [ ] **Step 3: 개발 서버로 수동 확인**

Run: `cd frontend && npm run dev`(이미 실행 중이 아니면)

브라우저에서 `/reviews/styles`를 열어 새 카드가 에러 없이 렌더링되는지
확인한다. 지금 DB에 `needs_confirmation=true`인 `StoreStyleProfile` 행이
없으면(Task 1 마이그레이션이 기존 행을 전부 지웠으므로 거의 확실히
없다) 카드가 그냥 안 보이는 게 정상이다(컴포넌트가 `null`을 반환) — 다른
카드들(훈련카드, 답글 스타일 설정)의 레이아웃이 깨지지 않는지만 확인하면
된다.

- [ ] **Step 4: 커밋**

```bash
git add "frontend/src/app/(app)/reviews/styles/page.tsx"
git commit -m "feat: 답글 스타일 설정 화면에 AI 원칙 확인 카드 추가"
```

---

## 전체 플랜 완료 후 확인할 것 (사람이 검토)

- 새 마이그레이션 `0004`(기존 store_style_profile 전체 DROP 후 재생성)가
  Plan 1/2/3의 `0001`/`0002`/`0003`과 함께 아직 운영 DB에 반영 안 됨 —
  배포 시 `alembic upgrade head` 한 번으로 넷 다 올라간다. 이 작업은 새
  시드/백필 스크립트가 없다.
- 운영에 적용하기 전에, 운영 DB의 `store_style_profile`에 실제로 몇 개
  매장의 데이터가 들어있는지 한 번 확인해보는 게 좋다 — 이 플랜의
  마이그레이션이 그 데이터를 전부 지우고, 다음 저장 시점(사장님이 답글을
  고쳐 쓸 때)에야 카테고리별로 자연히 다시 쌓인다는 걸 미리 알고 있는 게
  좋다(Global Constraints에 이미 적어둔 의도된 동작).
