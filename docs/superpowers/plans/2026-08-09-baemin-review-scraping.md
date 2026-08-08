# 배민 리뷰 실데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "가게 연결" 화면에서 배민 사장님광장에 실제 ID/PW로 로그인하고, Playwright 기반 크롤러로 실제 리뷰를 긁어와 우리 사이트가 실제 쓰는 PostgreSQL DB에 적재한다.

**Architecture:** 리뷰는 주문과 완전히 독립적으로 다룬다(조사 결과 배민 리뷰 API와 주문내역 API 사이에 연결 키가 없음을 확인했다) — `reviews` 테이블에 `store_id`/`platform_id`/`menu_summary`를 직접 추가하고 `order_id`는 선택적 FK로 강등한다. 배민 로그인은 `backend/scrapers/` 아래 Playwright 기반 로그인 모듈이 인증 세션을 만들고, 같은 세션의 `request_context`로 `self-api.baemin.com` 리뷰 API를 직접 HTTP 호출한다. 자격증명은 Fernet으로 암호화해 저장하고, 리뷰 동기화는 `POST` 즉시 응답 + FastAPI `BackgroundTasks` + 폴링 엔드포인트로 처리한다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API), cryptography(Fernet), Next.js App Router.

## Global Constraints

- 배민만, 리뷰만. 주문/정산 실데이터 연동과 쿠팡이츠/요기요는 이번 범위 밖.
- 리뷰와 주문/정산을 DB에서 억지로 연결하지 않는다 — `reviews.order_id`는 선택적 FK로 남기되 이번 범위에서 실제로 채워지는 경우는 없다.
- 자격증명(배민 ID/PW, `CREDENTIAL_ENCRYPTION_KEY`)은 절대 하드코딩하거나 커밋하지 않는다 — 환경변수로만 다루고 로그에도 남기지 않는다(카카오 시크릿·Resend 키와 동일한 원칙).
- 로그인 폼 선택자와 "내 매장 목록" API는 스크립트가 추측하지 않는다 — 구현 시점에 실제 화면을 열어 확인한다(`crawler/`의 기존 원칙과 동일).
- 한 배민 계정에 매장이 여러 개면 첫 번째 매장만 쓴다 — 매장 선택 UI는 만들지 않는다(복잡한 권한/다중 사업자 관리 금지 원칙과 일치).
- 로그인 자동화 자체(Playwright 로그인 함수)는 실제 네트워크·실제 계정이 필요해 자동화된 pytest로 덮지 않는다 — 얇게 분리해서 그 아래(API 호출·매핑·DB 적재) 로직만 촘촘히 테스트한다. 로그인은 각 태스크에서 명시한 수동 검증으로 확인한다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 전체 재생성 방식의 DB 정본이다. 로컬은 `schema.sql`/`seed.sql` 전체 재실행, 운영(프로덕션)은 증분 `ALTER TABLE`만 실행한다.
- 참고 스펙: `docs/superpowers/specs/2026-08-09-baemin-review-scraping-design.md`

---

### Task 1: 데이터 모델 — reviews를 주문과 독립시키기

**Files:**
- Modify: `schema.sql` (reviews 테이블, store_platform_connections 테이블, 새 review_sync_jobs 테이블, DROP TABLE 목록, 헤더 테이블 개수)
- Modify: `backend/app/models.py` (Review, StorePlatformConnection, 새 ReviewSyncJob, 헤더 테이블 개수)
- Modify: `backend/app/routers/reviews.py` (order 경유 대신 review.store/review.platform/review.menu_summary 직접 사용)
- Modify: `seed.sql` (reviews INSERT에 store_id/platform_id/menu_summary 추가)
- Modify: `backend/tests/test_reviews.py` (`make_review` 헬퍼가 store_id/platform_id/menu_summary를 직접 채우도록)
- Test: `backend/tests/test_reviews.py` (신규 회귀 테스트 추가)

**Interfaces:**
- Produces: `Review.store_id: int`, `Review.platform_id: int`, `Review.menu_summary: str`, `Review.external_review_id: int | None`(UNIQUE), `Review.order_id: int | None`(더 이상 NOT NULL 아님). `StorePlatformConnection.credential_ciphertext: str | None`. 새 `ReviewSyncJob` 모델(`id, store_id, platform_id, status, reviews_fetched, reviews_inserted, error_message, started_at, finished_at`). 이후 모든 태스크가 이 컬럼/모델을 그대로 쓴다.

- [ ] **Step 1: `schema.sql` — DROP TABLE 목록과 헤더에 신규 테이블 반영**

`schema.sql:4`를 다음으로 교체:

```sql
-- 19개 테이블. 모든 FK에 ON DELETE 정책 명시.
```

`schema.sql:21-26`(`DROP TABLE IF EXISTS ...` 블록)을 다음으로 교체:

```sql
DROP TABLE IF EXISTS
    review_sync_jobs, signup_verifications, social_accounts, alerts, ad_rank_snapshots,
    ad_performance_metrics, ad_campaigns, repurchase_metrics, daily_settlements, review_replies,
    reviews, orders, reply_settings, reply_styles, subscriptions, store_platform_connections,
    platforms, stores, users
CASCADE;
```

- [ ] **Step 2: `schema.sql` — `store_platform_connections`에 `credential_ciphertext` 추가**

`schema.sql:66-77`(4번 테이블 블록 전체)을 다음으로 교체:

```sql
-- ----------------------------------------------------------------------------
-- 4. store_platform_connections — 매장:플랫폼 N:M 중간 테이블 (가게 연결 화면)
--    credential_ciphertext: 배민 등 실계정 로그인을 위한 암호화된 자격증명
--    (Fernet, JSON {"login_id", "password"}). 다른 플랫폼(Mock)은 NULL.
-- ----------------------------------------------------------------------------
CREATE TABLE store_platform_connections (
    id                    BIGSERIAL PRIMARY KEY,
    store_id              BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id           INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    platform_store_id     VARCHAR(30) NOT NULL,       -- Mock 스토어 아이디(배민은 실제 shopNo)
    business_number       VARCHAR(20),                -- Mock 사업자번호
    credential_ciphertext TEXT,                       -- 신규. 배민 실계정 암호화 자격증명
    connected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, platform_id)
);
```

- [ ] **Step 3: `schema.sql` — `reviews` 테이블을 주문과 독립시키기**

`schema.sql:139-155`(9번 테이블 블록 전체 + 인덱스)을 다음으로 교체:

```sql
-- ----------------------------------------------------------------------------
-- 9. reviews — 리뷰. store_id/platform_id/menu_summary를 직접 갖는다(주문과
--    독립적으로 적재 가능 — 배민 리뷰 API에는 주문과 연결할 공통 키가 없다).
--    order_id는 있으면 연결하는 선택적 FK로 남겨둔다(현재는 채워지는 경우 없음).
--    external_review_id: 배민 리뷰 API의 id. 재동기화 시 중복 판별 키. Mock은 NULL.
--    상태: unanswered(미등록) → pending(등록 대기: AI 초안 생성됨) → answered(등록 완료)
-- ----------------------------------------------------------------------------
CREATE TABLE reviews (
    id                   BIGSERIAL PRIMARY KEY,
    order_id             BIGINT      UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    store_id             BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id          INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    menu_summary         VARCHAR(200) NOT NULL,
    external_review_id   BIGINT      UNIQUE,
    rating               SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    content              TEXT        NOT NULL,
    customer_nickname    VARCHAR(50) NOT NULL,    -- 닉네임만 저장, 실명 아님
    customer_order_count INT         NOT NULL DEFAULT 1,  -- 이 고객의 누적 주문 횟수 (n회 주문 표시)
    status               VARCHAR(12) NOT NULL DEFAULT 'unanswered'
                         CHECK (status IN ('unanswered', 'pending', 'answered')),
    created_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_reviews_status ON reviews(status);
CREATE INDEX idx_reviews_store  ON reviews(store_id);
```

(Postgres UNIQUE 컬럼은 NULL을 여러 개 허용하므로 `order_id UNIQUE`에서 `NOT NULL`만 빼면 된다 — 값이 있을 때만 유일성이 강제된다.)

- [ ] **Step 4: `schema.sql` — 새 테이블 `review_sync_jobs` 추가**

파일 맨 끝(`schema.sql`의 `signup_verifications` 블록 다음, `COMMIT;` 전 — 현재 313번째 줄이 파일 끝인지 `tail -5 schema.sql`로 확인 후 그 뒤에)에 추가:

```sql

-- ----------------------------------------------------------------------------
-- 19. review_sync_jobs — 배민 리뷰 동기화 작업 상태 추적 (가게 연결 화면의
--     "리뷰 동기화" 버튼 → 백그라운드 작업 → 폴링)
-- ----------------------------------------------------------------------------
CREATE TABLE review_sync_jobs (
    id               BIGSERIAL PRIMARY KEY,
    store_id         BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id      INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'success', 'failed')),
    reviews_fetched  INT,
    reviews_inserted INT,
    error_message    TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);
```

파일이 `COMMIT;`으로 끝나는지 확인한다(끝나지 않으면 새 테이블 CREATE 문 뒤, 기존 `COMMIT;` 앞에 삽입되도록 위치를 조정한다).

- [ ] **Step 5: `backend/app/models.py` — 헤더 테이블 개수 갱신**

`backend/app/models.py:1`의 `"""SQLAlchemy 모델 — schema.sql의 18개 테이블과 1:1 대응.` 를 다음으로 교체:

```python
"""SQLAlchemy 모델 — schema.sql의 19개 테이블과 1:1 대응.
```

- [ ] **Step 6: `backend/app/models.py` — `StorePlatformConnection`에 `credential_ciphertext` 추가**

`class StorePlatformConnection` 안의 `business_number: Mapped[str | None] = mapped_column(String(20))` 다음 줄에 삽입:

```python
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 7: `backend/app/models.py` — `Review` 모델을 주문 독립형으로 변경**

`class Review` 전체를 다음으로 교체:

```python
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), unique=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    menu_summary: Mapped[str] = mapped_column(String(200))
    external_review_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), unique=True
    )
    rating: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    customer_nickname: Mapped[str] = mapped_column(String(50))
    customer_order_count: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(12), default="unanswered")
    created_at: Mapped[datetime]

    order: Mapped[Order | None] = relationship(back_populates="review")
    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()
    replies: Mapped[list["ReviewReply"]] = relationship(back_populates="review")
```

- [ ] **Step 8: `backend/app/models.py` — 새 `ReviewSyncJob` 모델 추가**

`class Review` 다음, `class ReviewReply` 앞에 삽입:

```python


class ReviewSyncJob(Base):
    __tablename__ = "review_sync_jobs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    reviews_fetched: Mapped[int | None]
    reviews_inserted: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()
```

- [ ] **Step 9: 백엔드 테스트 실행 — 여기서 실패하는 게 정상**

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py -v`
Expected: FAIL — `test_reviews.py`의 `make_review` 헬퍼가 `Review(order_id=..., ...)`만 채우고 `store_id`/`platform_id`/`menu_summary`를 안 채워서 `NOT NULL constraint failed` 에러가 난다.

- [ ] **Step 10: `backend/tests/test_reviews.py` — `make_review` 헬퍼를 주문 독립형으로 변경**

파일 3번째 줄 `from app.models import Order, Review`를 다음으로 교체(1번째 줄의
기존 `from datetime import datetime, timezone`은 그대로 둔다 — 중복 추가하지 않는다):

```python
from app.models import Review
```

`make_review` 함수 전체(`def make_review(...):` 부터 `return review`까지)를 다음으로 교체:

```python
def make_review(db_session, store, platforms, rating, content="테스트 리뷰"):
    review = Review(
        store_id=store.id, platform_id=platforms["baemin"].id, menu_summary="양념치킨",
        rating=rating, content=content, customer_nickname="먹보",
        customer_order_count=2, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()
    return review
```

(`Order` import와 `order` 생성 코드를 통째로 제거한다 — 더 이상 필요 없다.)

- [ ] **Step 11: `backend/app/routers/reviews.py` — order 경유를 review 직접 참조로 교체**

파일 전체를 다음으로 교체:

```python
"""리뷰 관리 + 답글 스타일. 답글 생성은 템플릿 기반 Mock — 실제 AI 호출 없음."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import ReplyStyle, Review, ReviewReply, Store, User

router = APIRouter(tags=["reviews"])


@router.get("/reply-styles")
def list_reply_styles(db: Session = Depends(get_db)):
    styles = db.scalars(select(ReplyStyle).order_by(ReplyStyle.id)).all()
    return [{"id": s.id, "name": s.name, "description": s.description} for s in styles]


def _band(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


def _fill_template(template: str, review: Review, store: Store) -> str:
    return (
        template.replace("{nickname}", review.customer_nickname)
        .replace("{menu}", review.menu_summary)
        .replace("{store}", store.name)
    )


@router.get("/reviews")
def list_reviews(
    status: str | None = None,
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    stmt = (
        select(Review)
        .where(Review.store_id == sid)
        .options(joinedload(Review.platform), joinedload(Review.replies))
        .order_by(Review.created_at.desc())
    )
    if status:
        stmt = stmt.where(Review.status == status)

    reviews = db.scalars(stmt).unique().all()
    result = []
    for r in reviews:
        final_reply = next((rp for rp in r.replies if rp.reply_type == "final"), None)
        draft_reply = next((rp for rp in r.replies if rp.reply_type == "ai_draft"), None)
        secondary_replies = [rp for rp in r.replies if rp.reply_type == "secondary"]
        result.append({
            "id": r.id,
            "order_id": r.order_id,
            "platform_name": r.platform.name,
            "menu_summary": r.menu_summary,
            "rating": r.rating,
            "content": r.content,
            "customer_nickname": r.customer_nickname,
            "customer_order_count": r.customer_order_count,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "final_reply": {"content": final_reply.content, "style_id": final_reply.style_id} if final_reply else None,
            "draft_reply": {"content": draft_reply.content, "style_id": draft_reply.style_id} if draft_reply else None,
            "secondary_replies": [
                {"id": rp.id, "content": rp.content, "created_at": rp.created_at.isoformat()}
                for rp in sorted(secondary_replies, key=lambda rp: rp.created_at)
            ],
        })
    return result


class GenerateReplyRequest(BaseModel):
    style_id: int


@router.post("/reviews/{review_id}/generate-reply")
def generate_reply(
    review_id: int, body: GenerateReplyRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")

    style = db.get(ReplyStyle, body.style_id)
    if style is None:
        raise HTTPException(404, "답글 스타일 없음")

    template = {"low": style.template_low, "mid": style.template_mid, "high": style.template_high}[_band(review.rating)]
    content = _fill_template(template, review, review.store)

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id}


class SaveReplyRequest(BaseModel):
    style_id: int
    content: str


@router.post("/reviews/{review_id}/reply")
def save_final_reply(
    review_id: int, body: SaveReplyRequest,
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
    db.commit()
    return {"id": reply.id, "content": reply.content}


class SecondaryReplyRequest(BaseModel):
    content: str


@router.post("/reviews/{review_id}/secondary-reply")
def add_secondary_reply(
    review_id: int, body: SecondaryReplyRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """답글 완료 리뷰에 덧붙이는 2차(추가) 답글. 고객이 리뷰를 수정했거나 추가 안내가 필요할 때 사용."""
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")
    if review.status != "answered":
        raise HTTPException(409, "1차 답글이 등록된 리뷰에만 2차 답글을 추가할 수 있습니다")

    reply = ReviewReply(
        review_id=review.id, reply_type="secondary", style_id=None,
        content=body.content, created_at=datetime.now(timezone.utc),
    )
    db.add(reply)
    db.commit()
    return {"id": reply.id, "content": reply.content}
```

- [ ] **Step 12: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py -v`
Expected: 기존 4개 테스트 전부 PASS.

- [ ] **Step 13: 회귀 테스트 추가 — 주문 없는 리뷰(배민 스크래핑 시나리오)도 정상 동작하는지**

`backend/tests/test_reviews.py` 파일 끝에 추가:

```python
def test_review_without_order_is_listed_and_repliable(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    """order_id 없이(배민 스크래핑처럼) 만든 리뷰도 정상 조회/답글 생성이 되는지 확인."""
    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="양념치킨",
        rating=5, content="주문 연결 없는 리뷰", customer_nickname="먹보",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    listed = client.get("/reviews", headers=auth_headers).json()
    matched = next(r for r in listed if r["id"] == review.id)
    assert matched["order_id"] is None
    assert matched["menu_summary"] == "양념치킨"

    res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert res.status_code == 200
    assert "양념치킨" in res.json()["content"]
```

- [ ] **Step 14: 테스트 실행**

Run: `cd backend && .venv/bin/pytest tests/test_reviews.py -v -k without_order`
Expected: PASS

- [ ] **Step 15: `seed.sql` — reviews INSERT에 store_id/platform_id/menu_summary 채우기**

`seed.sql:126-155`(`INSERT INTO reviews ...` 문 전체)을 다음으로 교체:

```sql
INSERT INTO reviews (order_id, store_id, platform_id, menu_summary, rating, content, customer_nickname, customer_order_count, status, created_at)
SELECT
    o.id,
    o.store_id,
    o.platform_id,
    o.menu_summary,
    r.rating,
    CASE
      WHEN r.rating >= 4 THEN (ARRAY['진짜 맛있어요. 재주문 의사 100%입니다','바삭하고 양도 푸짐해요. 최고!','배달도 빠르고 포장도 꼼꼼했어요','잘 먹겠습니다^^ 사진 보세요 비주얼 대박','단골 확정입니다. 늘 한결같아요'])[1 + floor(random()*5)::int]
      WHEN r.rating  = 3 THEN (ARRAY['맛은 있는데 배달이 좀 늦었어요','무난해요. 가끔 시켜먹기 좋아요','양이 조금 줄어든 느낌이네요'])[1 + floor(random()*3)::int]
      ELSE                    (ARRAY['식어서 왔어요. 실망입니다','주문한 거랑 다른 메뉴가 왔어요','소스가 안 왔어요. 확인 부탁드립니다'])[1 + floor(random()*3)::int]
    END,
    (ARRAY['먹보','치킨러버','동네주민','야식왕','리뷰요정','단골손님','오늘은머냐','맛잘알'])[1 + floor(random()*8)::int],
    1 + floor(random() * 8)::int,
    CASE WHEN r.rn <= 20 THEN 'answered' WHEN r.rn <= 22 THEN 'pending' ELSE 'unanswered' END,
    o.ordered_at + make_interval(hours => 1 + floor(random() * 5)::int)
FROM (
    -- 주의: 선택(ORDER BY random())과 별점(random())을 같은 쿼리 레벨에 두면
    -- PostgreSQL이 두 random()을 동일 표현식으로 통합해 상관관계가 생긴다.
    -- MATERIALIZED CTE로 선택을 먼저 고정한 뒤 별점을 따로 뽑는다.
    WITH picked AS MATERIALIZED (
        SELECT id
        FROM orders
        WHERE ordered_at >= CURRENT_DATE - 30
        ORDER BY random()
        LIMIT 40
    )
    SELECT id,
           row_number() OVER (ORDER BY id) AS rn,
           CASE WHEN p < 0.55 THEN 5 WHEN p < 0.75 THEN 4 WHEN p < 0.85 THEN 3 WHEN p < 0.93 THEN 2 ELSE 1 END AS rating
    FROM (SELECT id, random() AS p FROM picked) x
) r
JOIN orders o ON o.id = r.id;
```

(컬럼 목록에 `store_id, platform_id, menu_summary`가 추가되고 SELECT 목록에 `o.store_id, o.platform_id, o.menu_summary`가 추가된 것 외에는 기존 로직과 동일하다.)

- [ ] **Step 16: 로컬 DB에 전체 재적용해서 seed.sql까지 검증**

```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql
```
Expected: 에러 없이 완료(로컬 데이터는 초기화된다). 5432 포트가 이미 점유돼 있으면 임시로 다른 포트를 써도 무방하다(이전 태스크들과 동일한 상황).

- [ ] **Step 17: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 18: 커밋**

```bash
git add schema.sql seed.sql backend/app/models.py backend/app/routers/reviews.py backend/tests/test_reviews.py
git commit -m "feat: reviews를 order와 독립적으로 적재 가능하게 데이터 모델 변경"
```

---

### Task 2: 자격증명 암호화

**Files:**
- Create: `backend/app/credential_crypto.py`
- Test: `backend/tests/test_credential_crypto.py`
- Modify: `backend/requirements.txt` (`cryptography` 추가)

**Interfaces:**
- Consumes: 없음(독립 모듈).
- Produces: `encrypt_credential(login_id: str, password: str) -> str`, `decrypt_credential(ciphertext: str) -> dict[str, str]`(키: `login_id`, `password`), `CredentialCryptoError`. Task 4가 이 세 개를 그대로 가져다 쓴다.

- [ ] **Step 1: `cryptography` 패키지 설치**

`backend/requirements.txt` 끝에 추가:

```
cryptography
```

Run: `cd backend && .venv/bin/pip install cryptography`
Expected: 설치 성공.

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_credential_crypto.py` 신규 생성:

```python
import pytest
from cryptography.fernet import Fernet

from app import credential_crypto as cc


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    ciphertext = cc.encrypt_credential("test_baemin_id", "test-pass-123!")
    assert "test-pass-123" not in ciphertext

    decrypted = cc.decrypt_credential(ciphertext)
    assert decrypted == {"login_id": "test_baemin_id", "password": "test-pass-123!"}


def test_encrypt_without_key_raises(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(cc.CredentialCryptoError):
        cc.encrypt_credential("id", "pw")


def test_decrypt_with_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = cc.encrypt_credential("id", "pw")

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(cc.CredentialCryptoError):
        cc.decrypt_credential(ciphertext)


def test_decrypt_garbage_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(cc.CredentialCryptoError):
        cc.decrypt_credential("not-a-real-token")
```

(테스트에는 실제 배민 자격증명이 아니라 `test_baemin_id`/`test-pass-123!` 같은 임의 더미 값만 쓴다.)

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_credential_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.credential_crypto'`

- [ ] **Step 4: `backend/app/credential_crypto.py` 구현**

```python
"""배민 등 실계정 자격증명을 Fernet 대칭키로 암호화/복호화한다.

원문 ID/PW는 로그에 남기지 않는다 — 카카오 시크릿·Resend 키와 동일하게
환경변수(CREDENTIAL_ENCRYPTION_KEY)로만 다룬다.
"""

import json
import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialCryptoError(Exception):
    pass


def _get_fernet() -> Fernet:
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise CredentialCryptoError("CREDENTIAL_ENCRYPTION_KEY 환경변수가 설정되지 않았습니다")
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise CredentialCryptoError("CREDENTIAL_ENCRYPTION_KEY 형식이 올바르지 않습니다") from e


def encrypt_credential(login_id: str, password: str) -> str:
    payload = json.dumps({"login_id": login_id, "password": password}).encode()
    return _get_fernet().encrypt(payload).decode()


def decrypt_credential(ciphertext: str) -> dict[str, str]:
    try:
        payload = _get_fernet().decrypt(ciphertext.encode())
    except InvalidToken as e:
        raise CredentialCryptoError("자격증명 복호화 실패") from e
    return json.loads(payload)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_credential_crypto.py -v`
Expected: 4개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/requirements.txt backend/app/credential_crypto.py backend/tests/test_credential_crypto.py
git commit -m "feat: Fernet 기반 자격증명 암호화 모듈 추가"
```

---

### Task 3: 배민 스크래퍼 — 로그인 + 리뷰 조회/매핑

**Files:**
- Create: `backend/scrapers/__init__.py`
- Create: `backend/scrapers/baemin_auth.py`
- Create: `backend/scrapers/baemin_reviews.py`
- Test: `backend/tests/test_baemin_reviews.py`
- Modify: `backend/requirements.txt` (`playwright` 추가)

**Interfaces:**
- Consumes: 없음(외부 사이트와 직접 통신).
- Produces: `scrapers.baemin_auth.login(login_id: str, password: str, headless: bool = True) -> BaeminSession`(필드: `request_context`, `shop_no: int`, `shop_name: str`, 메서드 `close()`), `scrapers.baemin_auth.BaeminLoginError`. `scrapers.baemin_reviews.fetch_all_reviews(request_context, shop_no: int, date_from: str | None = None, date_to: str | None = None, limit: int = 20) -> list[dict]`, `scrapers.baemin_reviews.map_review(raw: dict, store_id: int, platform_id: int) -> dict`(반환 dict 키가 `Review` 모델 컬럼명과 정확히 일치), `scrapers.baemin_reviews.BaeminScrapeError`. Task 4가 이 함수들을 그대로 가져다 쓴다.

- [ ] **Step 1: `playwright` 설치 + 브라우저 바이너리 설치**

`backend/requirements.txt` 끝에 추가:

```
playwright
```

Run:
```bash
cd backend
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```
Expected: 설치 성공(브라우저 바이너리 다운로드 포함, 시간이 좀 걸릴 수 있다).

- [ ] **Step 2: 패키지 디렉토리 생성**

```bash
mkdir -p backend/scrapers
touch backend/scrapers/__init__.py
```

- [ ] **Step 3: 실제 로그인 화면에서 선택자 + "내 매장 목록" API 확인 (사람이 직접 로그인)**

이 프로젝트의 안전 원칙상 에이전트가 실제 비밀번호를 화면에 직접 입력하지 않는다 — 이 단계는 사용자가 직접 진행해야 한다. 아래 명령으로 Playwright Inspector를 연다:

```bash
cd backend
.venv/bin/playwright codegen https://self.baemin.com/login
```

사용자에게 다음을 요청한다:
1. 열린 브라우저 창에서 본인 배민 사장님광장 ID/PW로 직접 로그인하고, 로그인 성공 후 매장이 보이는 화면까지 이동한 뒤 창을 닫아달라고 안내한다. 창을 닫으면 터미널에 생성된 Python 코드가 출력된다 — 여기서 아이디 입력창/비밀번호 입력창/로그인 버튼의 실제 셀렉터를 확인한다.
2. 동시에(또는 별도로) 크롬 개발자도구 Network 탭에서 로그인 직후 페이지가 호출하는 "내 매장 목록" API의 정확한 URL과 응답 JSON 구조(어떤 필드가 `shopNo`/매장명인지)를 확인해달라고 요청한다 — 이전에 확인한 리뷰 API처럼 `self-api.baemin.com` 아래 별도 REST 엔드포인트일 가능성이 높다.

이 단계에서 확인한 값(아이디/비밀번호 입력창 셀렉터, 로그인 버튼 셀렉터, 매장 목록 API URL 패턴과 응답 필드명)을 다음 Step의 코드에 실제 값으로 반영한다 — 확인 전 값을 추측해서 넣지 않는다.

- [ ] **Step 4: `backend/scrapers/baemin_auth.py` 구현**

Step 3에서 확인한 실제 선택자/URL로 아래 템플릿의 `_ID_INPUT`, `_PASSWORD_INPUT`, `_SUBMIT_BUTTON`, `_capture_shop_list`의 URL 판별 조건, `first_shop["shopNo"]`/`first_shop.get("name", ...)` 부분을 교체해서 작성한다:

```python
"""Playwright로 배민 사장님광장(self.baemin.com)에 실제 로그인해 인증된 세션을 만든다.

로그인 성공 후 계정에 연결된 첫 번째 매장의 shopNo도 함께 확인해 반환한다(여러
매장이 있어도 매장 선택 UI는 만들지 않는다 — 범위 밖). 로그인 폼의 선택자와
"내 매장 목록" API는 추측하지 않고 실제 화면에서 확인한 값을 쓴다(Step 3 참고).
"""

from dataclasses import dataclass

from playwright.sync_api import sync_playwright

_LOGIN_URL = "https://self.baemin.com/login"

# Step 3에서 실제 로그인 화면을 열어 확인한 값으로 채운다.
_ID_INPUT = "input[name='id']"
_PASSWORD_INPUT = "input[name='password']"
_SUBMIT_BUTTON = "button[type='submit']"


class BaeminLoginError(Exception):
    pass


@dataclass
class BaeminSession:
    request_context: object
    shop_no: int
    shop_name: str
    _playwright: object
    _browser: object

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()


def _extract_login_error(page) -> str | None:
    for selector in ("[role='alert']", ".error-message"):
        el = page.query_selector(selector)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    return None


def login(login_id: str, password: str, headless: bool = True) -> BaeminSession:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context()
    page = context.new_page()

    shop_list_response: dict = {}

    def _capture_shop_list(response):
        # Step 3에서 확인한 "내 매장 목록" API의 URL 패턴으로 이 조건을 교체한다.
        if "shops" in response.url and response.request.method == "GET" and response.status == 200:
            try:
                shop_list_response["body"] = response.json()
            except ValueError:
                pass

    page.on("response", _capture_shop_list)

    try:
        page.goto(_LOGIN_URL)
        page.fill(_ID_INPUT, login_id)
        page.fill(_PASSWORD_INPUT, password)
        page.click(_SUBMIT_BUTTON)

        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
        except Exception as e:
            error_text = _extract_login_error(page)
            raise BaeminLoginError(error_text or "로그인에 실패했습니다. 잠시 후 다시 시도해주세요") from e

        page.wait_for_timeout(2_000)  # 매장 목록 API 응답을 기다린다
        shops = shop_list_response.get("body")
        if not shops:
            raise BaeminLoginError("매장 목록을 확인하지 못했습니다")

        # Step 3에서 확인한 실제 응답 구조(리스트인지, {"shops": [...]}인지 등)로 교체한다.
        shop_list = shops if isinstance(shops, list) else shops["shops"]
        first_shop = shop_list[0]

        return BaeminSession(
            request_context=context.request,
            shop_no=first_shop["shopNo"],
            shop_name=first_shop.get("name", ""),
            _playwright=playwright,
            _browser=browser,
        )
    except BaeminLoginError:
        browser.close()
        playwright.stop()
        raise
```

- [ ] **Step 5: 실제 계정으로 로그인 성공 수동 검증**

이 태스크의 Global Constraints에 따라 로그인 함수는 자동화된 pytest로 덮지 않는다 — 수동으로 검증한다. 자격증명은 파일에 쓰지 않고 커맨드라인 환경변수로만 넘긴다:

```bash
cd backend
BAEMIN_TEST_ID="<사용자에게 확인받은 실제 배민 ID>" \
BAEMIN_TEST_PW="<사용자에게 확인받은 실제 배민 비밀번호>" \
.venv/bin/python -c "
import os
from scrapers.baemin_auth import login
session = login(os.environ['BAEMIN_TEST_ID'], os.environ['BAEMIN_TEST_PW'], headless=False)
print('로그인 성공:', session.shop_no, session.shop_name)
session.close()
"
```
Expected: 실제 매장명과 shopNo가 출력된다(예: 치밥대장 숯불양념92치킨, 14804318). 실패하면 Step 3에서 확인한 선택자/URL을 다시 점검한다. `headless=False`라 캡차 등 예상 못한 화면이 뜨면 사람이 직접 확인할 수 있다.

- [ ] **Step 6: `backend/scrapers/baemin_reviews.py` 구현**

```python
"""배민 리뷰 API(HTML 파싱이 아니라 직접 HTTP 호출)에서 리뷰를 가져오고 우리
스키마 필드로 매핑한다. 인증은 baemin_auth.login()이 반환한 세션이 담당한다.
"""

from datetime import datetime, timedelta, timezone

_REVIEWS_URL_TEMPLATE = "https://self-api.baemin.com/v1/review/shops/{shop_no}/reviews"


class BaeminScrapeError(Exception):
    pass


def fetch_all_reviews(
    request_context, shop_no: int,
    date_from: str | None = None, date_to: str | None = None, limit: int = 20,
) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    date_from = date_from or (today - timedelta(days=730)).isoformat()
    date_to = date_to or today.isoformat()

    reviews: list[dict] = []
    offset = 0
    while True:
        resp = request_context.get(
            _REVIEWS_URL_TEMPLATE.format(shop_no=shop_no),
            params={"from": date_from, "to": date_to, "offset": offset, "limit": limit},
        )
        if resp.status != 200:
            raise BaeminScrapeError(f"리뷰 조회 실패: HTTP {resp.status}")
        body = resp.json()
        reviews.extend(body["reviews"])
        if not body.get("next"):
            break
        offset += limit
    return reviews


def map_review(raw: dict, store_id: int, platform_id: int) -> dict:
    menus = raw.get("menus") or []
    if not menus:
        menu_summary = "메뉴 정보 없음"
    elif len(menus) == 1:
        menu_summary = menus[0]["name"]
    else:
        menu_summary = f"{menus[0]['name']} 외 {len(menus) - 1}건"

    return {
        "external_review_id": raw["id"],
        "rating": round(raw["rating"]),
        "content": raw.get("contents") or "",
        "customer_nickname": raw["memberNickname"],
        "customer_order_count": raw.get("orderCount", 1),
        "menu_summary": menu_summary,
        "created_at": datetime.fromisoformat(raw["createdAt"]),
        "store_id": store_id,
        "platform_id": platform_id,
        "status": "unanswered",
    }
```

- [ ] **Step 7: 실패하는 테스트 작성**

`backend/tests/test_baemin_reviews.py` 신규 생성:

```python
from datetime import datetime

import pytest

from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review

_RAW_REVIEW = {
    "id": 2026080402827696,
    "rating": 5.0,
    "contents": "진짜 맛있어요 재주문할게요",
    "memberNickname": "먹보왕",
    "orderCount": 3,
    "menus": [{"name": "양념치킨"}],
    "createdAt": "2026-08-04T21:12:33+09:00",
    "displayStatus": "DISPLAY",
}


def test_map_review_translates_baemin_fields_to_our_schema():
    mapped = map_review(_RAW_REVIEW, store_id=7, platform_id=1)
    assert mapped == {
        "external_review_id": 2026080402827696,
        "rating": 5,
        "content": "진짜 맛있어요 재주문할게요",
        "customer_nickname": "먹보왕",
        "customer_order_count": 3,
        "menu_summary": "양념치킨",
        "created_at": datetime.fromisoformat("2026-08-04T21:12:33+09:00"),
        "store_id": 7,
        "platform_id": 1,
        "status": "unanswered",
    }


def test_map_review_summarizes_multiple_menus():
    raw = {**_RAW_REVIEW, "menus": [{"name": "양념치킨"}, {"name": "콜라 1.25L"}]}
    mapped = map_review(raw, store_id=7, platform_id=1)
    assert mapped["menu_summary"] == "양념치킨 외 1건"


def test_map_review_rounds_fractional_rating():
    raw = {**_RAW_REVIEW, "rating": 4.0}
    assert map_review(raw, store_id=7, platform_id=1)["rating"] == 4


def test_map_review_handles_empty_content():
    raw = {**_RAW_REVIEW, "contents": ""}
    assert map_review(raw, store_id=7, platform_id=1)["content"] == ""


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def json(self):
        return self._body


class _FakeRequestContext:
    def __init__(self, pages: list[dict]):
        self._pages = pages
        self.calls = []

    def get(self, url, params):
        self.calls.append(params)
        page = self._pages[params["offset"] // params["limit"]]
        return _FakeResponse(200, page)


def test_fetch_all_reviews_paginates_until_next_is_false():
    pages = [
        {"reviews": [_RAW_REVIEW], "next": True},
        {"reviews": [{**_RAW_REVIEW, "id": 999}], "next": False},
    ]
    ctx = _FakeRequestContext(pages)

    result = fetch_all_reviews(ctx, shop_no=14804318, limit=1)

    assert len(result) == 2
    assert [r["id"] for r in result] == [2026080402827696, 999]
    assert ctx.calls[0]["offset"] == 0
    assert ctx.calls[1]["offset"] == 1


def test_fetch_all_reviews_raises_on_non_200():
    class _FailingContext:
        def get(self, url, params):
            return _FakeResponse(401, {})

    with pytest.raises(BaeminScrapeError):
        fetch_all_reviews(_FailingContext(), shop_no=1)
```

- [ ] **Step 8: 테스트 실행 (`backend/` 디렉터리에서 실행 — `scrapers`가 최상위 패키지로 import되려면 cwd가 `backend/`여야 한다)**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_reviews.py -v`
Expected: 6개 테스트 전부 PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/requirements.txt backend/scrapers backend/tests/test_baemin_reviews.py
git commit -m "feat: 배민 사장님광장 로그인 + 리뷰 API 조회/매핑 스크래퍼 추가"
```

---

### Task 4: 백엔드 엔드포인트 — 배민 로그인/동기화/폴링

**Files:**
- Create: `backend/app/review_sync.py`
- Modify: `backend/app/routers/store_connections.py`
- Test: `backend/tests/test_store_connections.py`, `backend/tests/test_review_sync.py`(신규)

**Interfaces:**
- Consumes: Task 1의 `ReviewSyncJob`, `Review.external_review_id` 등. Task 2의 `encrypt_credential`/`decrypt_credential`/`CredentialCryptoError`. Task 3의 `scrapers.baemin_auth.login`/`BaeminLoginError`, `scrapers.baemin_reviews.fetch_all_reviews`/`map_review`/`BaeminScrapeError`.
- Produces: `POST /store-connections/baemin/login {platform_login_id, platform_login_password}` → `{"connected": true, "shop_name": str, "platform_store_id": str}`. `POST /store-connections/baemin/sync-reviews` → 202 `{"job_id": int}`. `GET /store-connections/baemin/sync-status/{job_id}` → `{"id", "status", "reviews_fetched", "reviews_inserted", "error_message"}`. `app.review_sync.sync_reviews_for_job(job, conn, db)`(DB 세션을 인자로 받아 테스트 가능), `app.review_sync.run_review_sync_job(job_id)`(BackgroundTasks가 실제로 호출하는 래퍼). Task 5(프론트엔드)가 이 3개 엔드포인트를 그대로 소비한다.

- [ ] **Step 1: `backend/app/review_sync.py` 구현**

```python
"""리뷰 동기화 백그라운드 작업 오케스트레이션 — 스크래핑 + 매핑 + DB 적재.

`sync_reviews_for_job`는 순수하게 주어진 DB 세션으로만 동작해 테스트가 쉽다.
`run_review_sync_job`는 FastAPI BackgroundTasks가 실제로 호출하는 얇은 래퍼로,
요청과 독립적인 자기 세션(SessionLocal)을 연다 — 요청이 끝나면 요청 스코프
세션은 이미 닫혀 있기 때문이다.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_crypto import decrypt_credential
from app.db import SessionLocal
from app.models import Review, ReviewSyncJob, StorePlatformConnection
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review


def sync_reviews_for_job(job: ReviewSyncJob, conn: StorePlatformConnection, db: Session) -> None:
    job.status = "running"
    db.commit()

    try:
        credential = decrypt_credential(conn.credential_ciphertext)
        session = baemin_login(credential["login_id"], credential["password"])
    except BaeminLoginError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    try:
        raw_reviews = fetch_all_reviews(session.request_context, session.shop_no)
    except BaeminScrapeError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return
    finally:
        session.close()

    mapped = [
        map_review(raw, store_id=job.store_id, platform_id=job.platform_id)
        for raw in raw_reviews
        if raw.get("displayStatus", "DISPLAY") == "DISPLAY"
    ]

    existing_ids = set(db.scalars(
        select(Review.external_review_id).where(Review.external_review_id.isnot(None))
    ).all())

    inserted = 0
    for m in mapped:
        if m["external_review_id"] in existing_ids:
            continue
        db.add(Review(**m))
        inserted += 1

    job.status = "success"
    job.reviews_fetched = len(mapped)
    job.reviews_inserted = inserted
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def run_review_sync_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ReviewSyncJob, job_id)
        conn = db.scalar(
            select(StorePlatformConnection).where(
                StorePlatformConnection.store_id == job.store_id,
                StorePlatformConnection.platform_id == job.platform_id,
            )
        )
        sync_reviews_for_job(job, conn, db)
    finally:
        db.close()
```

- [ ] **Step 2: `sync_reviews_for_job`에 대한 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 신규 생성:

```python
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import encrypt_credential
from app.models import Review, ReviewSyncJob, StorePlatformConnection
from app.review_sync import sync_reviews_for_job
from scrapers.baemin_auth import BaeminLoginError
from scrapers.baemin_reviews import BaeminScrapeError

_RAW_1 = {
    "id": 1001, "rating": 5.0, "contents": "이미 있는 리뷰", "memberNickname": "기존고객",
    "orderCount": 1, "menus": [{"name": "기존메뉴"}], "createdAt": "2026-08-01T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}
_RAW_2 = {
    "id": 1002, "rating": 4.0, "contents": "새 리뷰입니다", "memberNickname": "새고객",
    "orderCount": 2, "menus": [{"name": "새메뉴"}], "createdAt": "2026-08-02T10:00:00+09:00",
    "displayStatus": "DISPLAY",
}
_RAW_HIDDEN = {
    "id": 1003, "rating": 1.0, "contents": "숨김 리뷰", "memberNickname": "숨김고객",
    "orderCount": 1, "menus": [{"name": "메뉴"}], "createdAt": "2026-08-03T10:00:00+09:00",
    "displayStatus": "HIDDEN",
}


class _FakeSession:
    shop_no = 99999001
    request_context = object()
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture()
def sync_setup(db_session, seeded_user, platforms, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        status="pending", started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    return job, conn


def test_sync_inserts_new_reviews_and_skips_duplicates_and_hidden(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="기존메뉴",
        external_review_id=1001, rating=5, content="이미 있는 리뷰", customer_nickname="기존고객",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(
        review_sync_mod, "fetch_all_reviews",
        lambda request_context, shop_no: [_RAW_1, _RAW_2, _RAW_HIDDEN],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert job.reviews_fetched == 2  # HIDDEN 리뷰는 제외
    assert job.reviews_inserted == 1  # id=1001은 중복 스킵
    assert fake_session.closed is True

    inserted = db_session.query(Review).filter_by(external_review_id=1002).one()
    assert inserted.customer_nickname == "새고객"
    assert inserted.menu_summary == "새메뉴"


def test_sync_records_login_failure(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup

    def _raise(login_id, password):
        raise BaeminLoginError("아이디 또는 비밀번호가 일치하지 않습니다")

    monkeypatch.setattr(review_sync_mod, "baemin_login", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert "일치하지 않습니다" in job.error_message
    assert job.finished_at is not None


def test_sync_records_fetch_failure_and_still_closes_session(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    def _raise(request_context, shop_no):
        raise BaeminScrapeError("리뷰 조회 실패: HTTP 500")

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "failed"
    assert "HTTP 500" in job.error_message
    assert fake_session.closed is True
```

- [ ] **Step 3: 테스트 실행**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v`
Expected: 3개 테스트 전부 PASS(Step 1의 `review_sync.py`가 이미 구현돼 있으므로 바로 통과해야 한다 — 실패하면 import 경로나 monkeypatch 대상 이름을 다시 확인한다).

- [ ] **Step 4: `backend/app/routers/store_connections.py`에 엔드포인트 3개 추가**

파일 상단 import 블록(`import random` ~ `router = APIRouter(...)` 전)을 다음으로 교체:

```python
"""가게 연결. 매장×플랫폼 N:M 연결을 관리한다.

배민은 실제 사장님광장 계정으로 로그인해 매장을 연결하고 리뷰를 동기화할 수
있다. 그 외 플랫폼(쿠팡이츠, 요기요)은 여전히 Mock — 연결하면 Mock 스토어
아이디/사업자번호가 즉석에서 만들어질 뿐, 실제 계정 연동은 하지 않는다.
"""

import random
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.credential_crypto import encrypt_credential
from app.db import get_db
from app.models import Platform, ReviewSyncJob, Store, StorePlatformConnection, User
from app.review_sync import run_review_sync_job
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login

router = APIRouter(tags=["store-connections"])
```

`_row` 함수를 다음으로 교체(응답에 `has_real_credential` 추가):

```python
def _row(c: StorePlatformConnection) -> dict:
    return {
        "id": c.id,
        "platform_id": c.platform_id,
        "platform_code": c.platform.code,
        "platform_name": c.platform.name,
        "brand_color": c.platform.brand_color,
        "platform_store_id": c.platform_store_id,
        "business_number": c.business_number,
        "has_real_credential": c.credential_ciphertext is not None,
        "connected_at": c.connected_at.isoformat(),
    }
```

파일 맨 끝(`disconnect_platform` 함수 다음)에 추가:

```python


class BaeminLoginRequest(BaseModel):
    platform_login_id: str
    platform_login_password: str


@router.post("/store-connections/baemin/login")
def baemin_login_endpoint(
    body: BaeminLoginRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if platform is None:
        raise HTTPException(500, "배민 플랫폼이 등록되어 있지 않습니다")

    try:
        session = baemin_login(body.platform_login_id, body.platform_login_password)
    except BaeminLoginError as e:
        raise HTTPException(401, str(e))
    shop_no, shop_name = session.shop_no, session.shop_name
    session.close()

    ciphertext = encrypt_credential(body.platform_login_id, body.platform_login_password)

    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None:
        conn = StorePlatformConnection(
            store_id=sid, platform_id=platform.id,
            platform_store_id=str(shop_no),
            connected_at=datetime.now(timezone.utc),
        )
        db.add(conn)
    else:
        conn.platform_store_id = str(shop_no)
    conn.credential_ciphertext = ciphertext
    db.commit()
    db.refresh(conn)
    return {"connected": True, "shop_name": shop_name, "platform_store_id": conn.platform_store_id}


@router.post("/store-connections/baemin/sync-reviews", status_code=202)
def start_review_sync(
    background_tasks: BackgroundTasks, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None or conn.credential_ciphertext is None:
        raise HTTPException(400, "먼저 배민 로그인이 필요합니다")

    job = ReviewSyncJob(
        store_id=sid, platform_id=platform.id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_review_sync_job, job.id)
    return {"job_id": job.id}


@router.get("/store-connections/baemin/sync-status/{job_id}")
def get_sync_status(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(ReviewSyncJob, job_id, options=[joinedload(ReviewSyncJob.store)])
    if job is None or job.store.user_id != user.id:
        raise HTTPException(404, "작업 없음")
    return {
        "id": job.id, "status": job.status,
        "reviews_fetched": job.reviews_fetched, "reviews_inserted": job.reviews_inserted,
        "error_message": job.error_message,
    }
```

- [ ] **Step 5: 기존 테스트가 여전히 통과하는지 확인 (`has_real_credential` 필드 추가로 인한 회귀 없는지)**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py -v`
Expected: 기존 5개 테스트 전부 PASS.

- [ ] **Step 6: 기존 테스트에 `has_real_credential` 검증 추가**

`backend/tests/test_store_connections.py`의 `test_list_connections_includes_seed_baemin`을 다음으로 교체:

```python
def test_list_connections_includes_seed_baemin(client, seeded_user, auth_headers):
    res = client.get("/store-connections", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["platform_code"] == "baemin"
    assert body[0]["platform_store_id"] == "MK-1"
    assert body[0]["has_real_credential"] is False
```

- [ ] **Step 7: 새 엔드포인트 실패 테스트 작성**

`backend/tests/test_store_connections.py` 파일 끝에 추가:

```python
def test_baemin_login_upgrades_existing_mock_connection(client, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    class _FakeSession:
        shop_no = 99999001
        shop_name = "테스트가게"
        closed = False

        def close(self):
            self.closed = True

    fake_session = _FakeSession()
    monkeypatch.setattr(sc, "baemin_login", lambda login_id, password: fake_session)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "test_pw_123"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["shop_name"] == "테스트가게"
    assert body["platform_store_id"] == "99999001"
    assert fake_session.closed is True

    listed = client.get("/store-connections", headers=auth_headers).json()
    baemin_conn = next(c for c in listed if c["platform_code"] == "baemin")
    assert baemin_conn["platform_store_id"] == "99999001"  # 시드의 Mock MK-1이 실제 값으로 교체됨
    assert baemin_conn["has_real_credential"] is True
    assert len(listed) == 1  # 새로 만들지 않고 기존 연결을 업그레이드


def test_baemin_login_failure_returns_401_with_baemin_message(client, seeded_user, platforms, auth_headers, monkeypatch):
    from app.routers import store_connections as sc
    from scrapers.baemin_auth import BaeminLoginError

    def _raise(login_id, password):
        raise BaeminLoginError("아이디 또는 비밀번호가 일치하지 않습니다")

    monkeypatch.setattr(sc, "baemin_login", _raise)

    res = client.post(
        "/store-connections/baemin/login",
        json={"platform_login_id": "test_id", "platform_login_password": "wrong"},
        headers=auth_headers,
    )
    assert res.status_code == 401
    assert "일치하지 않습니다" in res.json()["detail"]


def test_sync_reviews_requires_baemin_login_first(client, seeded_user, platforms, auth_headers):
    # seeded_user의 baemin 연결은 Mock(credential_ciphertext 없음)이라 동기화 불가
    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 400


def test_sync_reviews_creates_pending_job_and_dispatches_background_task(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import StorePlatformConnection
    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    calls = []
    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: calls.append(job_id))

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    assert calls == [job_id]  # BackgroundTasks가 올바른 job_id로 호출됨(TestClient는 응답 전 동기 실행)

    status = client.get(f"/store-connections/baemin/sync-status/{job_id}", headers=auth_headers).json()
    assert status["id"] == job_id
    assert status["status"] == "pending"  # run_review_sync_job을 no-op으로 바꿨으니 상태 변경 없음


def test_sync_status_forbidden_for_other_users_job(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timezone

    from app.auth import hash_password
    from app.models import ReviewSyncJob, Store, User

    other = User(email="rival2@test.com", password_hash=hash_password("x"), nickname="경쟁사장2", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="라이벌가게2", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_job = ReviewSyncJob(
        store_id=other_store.id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(other_job)
    db_session.commit()

    res = client.get(f"/store-connections/baemin/sync-status/{other_job.id}", headers=auth_headers)
    assert res.status_code == 404
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py -v`
Expected: 전체(기존 6개 + 새 5개 = 11개) PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/app/review_sync.py backend/app/routers/store_connections.py \
  backend/tests/test_review_sync.py backend/tests/test_store_connections.py
git commit -m "feat: 배민 로그인/리뷰 동기화/폴링 엔드포인트 추가"
```

---

### Task 5: 프론트엔드 — 가게 연결 화면에 배민 실 로그인 + 리뷰 동기화

**Files:**
- Modify: `frontend/src/app/(app)/account/stores/page.tsx`

**Interfaces:**
- Consumes: Task 4의 `POST /store-connections/baemin/login`, `POST /store-connections/baemin/sync-reviews`, `GET /store-connections/baemin/sync-status/{job_id}`. `GET /store-connections` 응답에 새로 추가된 `has_real_credential` 필드. 기존 `apiGet`, `apiPost`, `apiDelete`, `ApiError`(`@/lib/api`), `Card`, `Modal` 컴포넌트.
- Produces: 없음(최종 UI 계층).

이 파일은 현재 커밋되지 않은 이전 작업(Mock 로그인 모달 UI)이 이미 있다 — 그 위에 배민만 실제 로그인으로 분기하는 변경을 얹는다. 구현 전 `git diff "frontend/src/app/(app)/account/stores/page.tsx"`로 현재 상태를 확인한다(이 Step 1의 "전체 교체" 내용이 이미 그 변경을 포함하고 있으므로 그대로 덮어써도 안전하다).

- [ ] **Step 1: `frontend/src/app/(app)/account/stores/page.tsx` 전체를 아래 내용으로 교체**

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/Card";
import { Modal } from "@/components/Modal";
import { apiDelete, apiGet, apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Connection = {
  id: number;
  platform_id: number;
  platform_code: string;
  platform_name: string;
  brand_color: string | null;
  platform_store_id: string;
  business_number: string | null;
  has_real_credential: boolean;
  connected_at: string;
};
type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };
type SyncStatus = {
  id: number;
  status: "pending" | "running" | "success" | "failed";
  reviews_fetched: number | null;
  reviews_inserted: number | null;
  error_message: string | null;
};

export default function StoreConnectionsPage() {
  const { storeId } = useStoreContext();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!storeId) return;
    setConnections(await apiGet<Connection[]>(`/store-connections?store_id=${storeId}`));
  }, [storeId]);

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then(setPlatforms);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const connectedIds = new Set(connections.map((c) => c.platform_id));
  const available = platforms.filter((p) => !connectedIds.has(p.id));

  const [loginTarget, setLoginTarget] = useState<PlatformOption | null>(null);
  const [loginId, setLoginId] = useState("");
  const [loginPw, setLoginPw] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const openLogin = (p: PlatformOption) => {
    setLoginTarget(p);
    setLoginId("");
    setLoginPw("");
    setModalError(null);
  };
  const closeLogin = () => {
    if (connecting) return;
    setLoginTarget(null);
    setLoginId("");
    setLoginPw("");
  };

  const submitLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginTarget) return;
    setConnecting(true);
    setModalError(null);
    try {
      if (loginTarget.code === "baemin") {
        await apiPost(`/store-connections/baemin/login?store_id=${storeId}`, {
          platform_login_id: loginId,
          platform_login_password: loginPw,
        });
      } else {
        // Mock 로그인 — 입력한 아이디/비밀번호는 전송/저장되지 않는다. 실제 로그인 흐름처럼
        // 잠깐의 지연을 준 뒤 매장을 연결한다.
        await new Promise((r) => setTimeout(r, 700));
        await apiPost("/store-connections", { platform_id: loginTarget.id, store_id: storeId });
      }
      setLoginTarget(null);
      setLoginId("");
      setLoginPw("");
      load();
    } catch (e) {
      setModalError(e instanceof ApiError ? e.message : "연결에 실패했습니다");
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async (id: number) => {
    await apiDelete(`/store-connections/${id}`);
    load();
  };

  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startSync = async (connectionId: number) => {
    setSyncingId(connectionId);
    setSyncStatus(null);
    try {
      const { job_id } = await apiPost<{ job_id: number }>(
        `/store-connections/baemin/sync-reviews?store_id=${storeId}`
      );
      pollRef.current = setInterval(async () => {
        const status = await apiGet<SyncStatus>(`/store-connections/baemin/sync-status/${job_id}`);
        setSyncStatus(status);
        if (status.status === "success" || status.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setSyncingId(null);
        }
      }, 4000);
    } catch (e) {
      setSyncStatus({
        id: 0, status: "failed", reviews_fetched: null, reviews_inserted: null,
        error_message: e instanceof ApiError ? e.message : "동기화 시작에 실패했습니다",
      });
      setSyncingId(null);
    }
  };

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">가게 연결</h1>
        <p className="text-sm text-muted">
          매장 {connections.length}개 플랫폼에 연결되었습니다. 배민은 실제 사장님광장 계정으로 로그인해
          리뷰를 가져올 수 있습니다. 그 외 플랫폼은 Mock 연동이며 실제 계정 연동은 하지 않습니다.
        </p>
      </div>

      {available.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {available.map((p) => (
            <button
              key={p.id}
              onClick={() => openLogin(p)}
              className="rounded-lg border border-accent px-4 py-2 text-sm font-medium text-accent transition hover:bg-accent-soft"
            >
              + {p.name} 연결하기
            </button>
          ))}
        </div>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {connections.map((c) => (
          <Card key={c.id}>
            <div className="flex items-start justify-between">
              <div>
                <span
                  className="rounded px-2 py-0.5 text-xs font-medium"
                  style={{ backgroundColor: `${c.brand_color}26`, color: c.brand_color ?? undefined }}
                >
                  {c.platform_name}
                </span>
                <p className="mt-2 text-xs text-muted">스토어 아이디: {c.platform_store_id}</p>
                {c.business_number && <p className="text-xs text-muted">사업자번호: {c.business_number}</p>}
                {c.platform_code === "baemin" && !c.has_real_credential && (
                  <p className="mt-1 text-xs text-warning">
                    Mock 연결입니다. 실제 로그인하려면 연결 해제 후 다시 연결하세요.
                  </p>
                )}
              </div>
              <button
                onClick={() => disconnect(c.id)}
                className="rounded-lg border border-danger/40 px-3 py-1 text-xs text-danger transition hover:bg-danger-soft"
              >
                연결 해제
              </button>
            </div>

            {c.platform_code === "baemin" && c.has_real_credential && (
              <div className="mt-3 border-t border-border-subtle pt-3">
                <button
                  onClick={() => startSync(c.id)}
                  disabled={syncingId === c.id}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {syncingId === c.id ? "동기화 중..." : "리뷰 동기화"}
                </button>
                {syncStatus && (
                  <p className="mt-2 text-xs text-muted">
                    {syncStatus.status === "success" &&
                      `${syncStatus.reviews_fetched}개 중 ${syncStatus.reviews_inserted}개 신규 추가`}
                    {syncStatus.status === "failed" && (
                      <span className="text-danger">동기화 실패: {syncStatus.error_message}</span>
                    )}
                    {(syncStatus.status === "pending" || syncStatus.status === "running") && "진행 중..."}
                  </p>
                )}
              </div>
            )}
          </Card>
        ))}
        {connections.length === 0 && <p className="text-sm text-muted">연결된 플랫폼이 없습니다.</p>}
      </div>

      {loginTarget && (
        <Modal title={`${loginTarget.name} 사장님광장 로그인`} onClose={closeLogin}>
          <form onSubmit={submitLogin} className="space-y-4">
            <p className="text-xs text-muted">
              {loginTarget.code === "baemin"
                ? "배민 사장님광장 아이디로 실제 로그인합니다. 로그인에 성공하면 리뷰를 실제로 가져올 수 있습니다."
                : `${loginTarget.name} 사장님광장 아이디로 로그인하면 매장이 연결됩니다. (Mock — 입력한 아이디/비밀번호는 서버로 전송되거나 저장되지 않습니다)`}
            </p>
            <div>
              <label className="mb-1 block text-xs text-muted">아이디</label>
              <input
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
                required
                autoFocus
                disabled={connecting}
                placeholder="사장님광장 아이디"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호</label>
              <input
                type="password"
                value={loginPw}
                onChange={(e) => setLoginPw(e.target.value)}
                required
                disabled={connecting}
                placeholder="비밀번호"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              />
            </div>
            {modalError && <p className="text-xs text-danger">{modalError}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={closeLogin}
                disabled={connecting}
                className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface-2 disabled:opacity-60"
              >
                취소
              </button>
              <button
                type="submit"
                disabled={connecting}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white transition disabled:opacity-60"
                style={{ backgroundColor: loginTarget.brand_color ?? "#6d5ef5" }}
              >
                {connecting ? "연결 중..." : "로그인"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 에러 없음

(이 환경에서 `npx tsc`가 `rtk` 셸 훅에 가로채져 가짜 결과를 낼 수 있다 — 반드시 `node_modules/.bin/tsc`를 직접 호출한다.)

- [ ] **Step 3: 커밋**

```bash
git add "frontend/src/app/(app)/account/stores/page.tsx"
git commit -m "feat: 가게 연결 화면에 배민 실 로그인 + 리뷰 동기화 UI 추가"
```

---

### Task 6: 배포 설정 + CLAUDE.md 갱신 + 실계정 로컬 검증

**Files:**
- Modify: `backend/railway.json`
- Modify: `backend/.env.example`
- Modify: `CLAUDE.md`
- Test: 없음(설정/문서 + 수동 검증)

**Interfaces:**
- Consumes: 이전 태스크 전체(엔드투엔드 검증 대상).
- Produces: 없음(배포/문서 최종 단계).

- [ ] **Step 1: `backend/railway.json` — Playwright 브라우저 바이너리 빌드 단계 추가**

`backend/railway.json` 전체를 다음으로 교체:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "RAILPACK",
    "buildCommand": "pip install -r requirements.txt && playwright install --with-deps chromium"
  },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

(Railpack이 `buildCommand`를 완전히 대체하는지 기존 자동 설치 단계에 이어붙이는지는 실제 배포 로그로 확인해야 한다 — Step 8에서 `mcp__railway__get_logs`로 반드시 확인한다. 만약 Railpack이 Python 의존성을 자동 설치하지 않게 되면 빌드가 깨지므로, 그 경우 `buildCommand`를 `playwright install --with-deps chromium`만 남기고 `pip install`은 Railpack 자동 단계에 맡기는 방향으로 조정한다.)

- [ ] **Step 2: `backend/.env.example`에 `CREDENTIAL_ENCRYPTION_KEY` 추가**

파일 끝에 추가:

```
# 배민 등 실계정 자격증명 암호화 (가게 연결 → 배민 실 로그인 기능). Fernet 키 —
# 배포 시 1회만 생성해서 재사용한다:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIAL_ENCRYPTION_KEY=
```

- [ ] **Step 3: `CLAUDE.md` — "절대 금지" 목록에서 배민을 뺀다**

`CLAUDE.md:29-30`을 다음으로 교체:

```
- 실제 쿠팡이츠/요기요 API 연동 금지 (배민은 예외 허용 — 아래 "배민 리뷰 연동" 절 참고)
- 실제 리뷰 크롤링 금지 (배민 리뷰는 예외 허용 — 아래 "배민 리뷰 연동" 절 참고)
```

- [ ] **Step 4: `CLAUDE.md` — "### 배민 리뷰 연동 (예외 허용)" 절 추가**

`CLAUDE.md`의 "### 이메일 인증 (실제 발송, 예외 허용)" 절 끝(`### 모바일 앱 (예외 허용)` 바로 앞)에 삽입:

```markdown
### 배민 리뷰 연동 (예외 허용)
원래 "실제 배민/쿠팡이츠/요기요 등 플랫폼 API 연동 금지", "실제 리뷰 크롤링
금지"였으나, 실 SaaS 전환 로드맵 3번(실제 배달 플랫폼 데이터 연동)의 첫
단계로 배민 리뷰만 실제로 연동하기로 결정했다(2026-08-09). 실제 계정으로
크롬 개발자도구 Network 탭을 직접 확인해, 사장님광장(self.baemin.com)의
리뷰 API(`self-api.baemin.com/v1/review/shops/{shopNo}/reviews`)와 주문내역
API 사이에 서로를 연결하는 공통 키가 없다는 걸 확인했다 — 그래서 리뷰를
주문/정산과 억지로 연결하지 않고, `reviews` 테이블이 `store_id`/
`platform_id`/`menu_summary`를 직접 갖도록 데이터 모델을 바꿨다(`order_id`는
있으면 연결하는 선택적 FK로 강등, 실제로 채워지는 경우는 없음).

"가게 연결" 화면에서 배민 카드만 실제 ID/PW를 받아 Playwright로 사장님광장에
로그인하고(`backend/scrapers/baemin_auth.py`), 로그인 세션의 request context로
리뷰 API를 직접 호출해(`backend/scrapers/baemin_reviews.py`) 실제 DB에
적재한다. 자격증명은 Fernet으로 암호화해 저장한다
(`backend/app/credential_crypto.py`, `CREDENTIAL_ENCRYPTION_KEY` 환경변수).
쿠팡이츠/요기요는 아직 미승인이라 "절대 금지" 그대로 유지, 배민의 주문/정산
실데이터 연동과 리뷰 답글 실제 자동 등록도 여전히 범위 밖이다. 설계 상세는
`docs/superpowers/specs/2026-08-09-baemin-review-scraping-design.md` 참고.
```

- [ ] **Step 5: `CLAUDE.md` — DB 설계 절에 `review_sync_jobs` 반영**

`CLAUDE.md:87-92`를 다음으로 교체:

```markdown
## DB 설계 (19개 테이블)
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, review_replies, reply_styles, reply_settings,
daily_settlements, repurchase_metrics, ad_campaigns,
ad_performance_metrics, ad_rank_snapshots, alerts, social_accounts,
signup_verifications, review_sync_jobs.
```

`CLAUDE.md:101`(`- reviews: 리뷰(별점, 내용, 고객 닉네임, 주문 횟수, 상태).`)을 다음으로 교체:

```markdown
- reviews: 리뷰(별점, 내용, 고객 닉네임, 주문 횟수, 상태). store_id/platform_id/
  menu_summary를 직접 가진다 — 주문과 독립적으로 적재 가능(배민 리뷰 API에는
  주문과 연결할 공통 키가 없음). order_id는 있으면 연결하는 선택적 FK.
```

`CLAUDE.md:119`(signup_verifications 설명 문단) 다음 줄에 추가:

```markdown
- review_sync_jobs: 배민 리뷰 동기화 작업 상태(pending/running/success/failed).
  "가게 연결" 화면의 "리뷰 동기화" 버튼 → 백그라운드 작업 → 폴링에 쓰인다.
```

`CLAUDE.md:125`(`- orders 1:1 reviews (reviews.order_id가 orders.id 참조, 핵심 외래키)`)를 다음으로 교체:

```markdown
- orders 1:1 reviews (reviews.order_id, 선택적 FK — 현재 실제로 채워지는 경우 없음)
- reviews N:1 stores, reviews N:1 platforms (직접 참조, 주문 조인 없이 조회)
```

`CLAUDE.md:132`(`- users 1:N social_accounts`) 다음 줄에 추가:

```markdown
- review_sync_jobs는 store, platform 참조
```

- [ ] **Step 6: 커밋**

```bash
git add backend/railway.json backend/.env.example CLAUDE.md
git commit -m "docs: 배민 리뷰 연동 CLAUDE.md/배포 설정 반영"
```

- [ ] **Step 7: 로컬 실계정 검증**

```bash
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql
```

```bash
cd backend
CREDENTIAL_ENCRYPTION_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/delivery_insight" \
  .venv/bin/uvicorn app.main:app --reload
```
```bash
cd frontend
npm run dev
```

브라우저로 `http://localhost:3000` 접속 → 데모 계정 로그인 → "가게 연결" 화면 →
배민이 이미 Mock(MK-1)으로 연결돼 있으므로 먼저 "연결 해제" → "배민 연결하기"
클릭 → **사용자 본인이 직접** 실제 배민 ID/PW 입력 후 로그인 → 성공하면
스토어 아이디가 실제 shopNo로 바뀌는지 확인 → "리뷰 동기화" 클릭 → 폴링이
끝날 때까지 기다린 뒤 "N개 중 M개 신규 추가" 메시지 확인 → 리뷰 관리 화면으로
이동해 실제 리뷰가 보이는지 확인한다.

(로그인 자격증명 입력은 사용자 본인이 우리 앱의 폼에 직접 타이핑한다 — 에이전트가
대신 입력하지 않는다. 이 프로젝트 안전 원칙상 당연한 절차이며 이 기능의 정상적인
사용 흐름이기도 하다.)

- [ ] **Step 8: 전체 백엔드 테스트 최종 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 9: 배포 안내 (실행은 사용자 확인 후)**

로컬 검증이 끝나면 Railway 배포가 남는다. 프로덕션(공유 상태)을 바꾸는 작업이라
실행 전 반드시 사용자에게 확인받는다:

1. 프로덕션 Postgres에 증분 `ALTER TABLE`을 실행한다(**`schema.sql` 전체
   재실행 금지** — 기존 데이터가 전부 날아간다):
   ```sql
   ALTER TABLE store_platform_connections ADD COLUMN credential_ciphertext TEXT;

   ALTER TABLE reviews ALTER COLUMN order_id DROP NOT NULL;
   ALTER TABLE reviews ADD COLUMN store_id BIGINT REFERENCES stores(id) ON DELETE CASCADE;
   ALTER TABLE reviews ADD COLUMN platform_id INT REFERENCES platforms(id) ON DELETE RESTRICT;
   ALTER TABLE reviews ADD COLUMN menu_summary VARCHAR(200);
   ALTER TABLE reviews ADD COLUMN external_review_id BIGINT UNIQUE;
   -- 기존 행 백필: 이미 order_id로 연결된 Mock 리뷰는 주문에서 그대로 채운다
   UPDATE reviews r SET store_id = o.store_id, platform_id = o.platform_id, menu_summary = o.menu_summary
   FROM orders o WHERE r.order_id = o.id;
   ALTER TABLE reviews ALTER COLUMN store_id SET NOT NULL;
   ALTER TABLE reviews ALTER COLUMN platform_id SET NOT NULL;
   ALTER TABLE reviews ALTER COLUMN menu_summary SET NOT NULL;
   CREATE INDEX idx_reviews_store ON reviews(store_id);

   CREATE TABLE review_sync_jobs (
       id               BIGSERIAL PRIMARY KEY,
       store_id         BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
       platform_id      INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
       status           VARCHAR(10) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'success', 'failed')),
       reviews_fetched  INT,
       reviews_inserted INT,
       error_message    TEXT,
       started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
       finished_at      TIMESTAMPTZ
   );
   ```
   실행 전 반드시 사용자에게 다시 확인받는다. (프로덕션에 이미 Mock 리뷰가
   있다면 `orders` 조인 백필로 채워지지만, 이 저장소는 프로덕션에 실사용자
   데이터가 없는 데모 환경이므로 `orders`가 비어 있다면 백필 UPDATE는
   0건이어도 무방하다 — 그 경우 `SET NOT NULL` 전에 프로덕션 `reviews`가
   실제로 비어 있는지 먼저 확인한다.)
2. Railway `backend` 서비스에 `CREDENTIAL_ENCRYPTION_KEY`를 새로 생성해서
   등록한다(로컬 Step 7에서 쓴 것과 같은 명령으로 생성, 로컬용과 다른 값을
   운영에 별도로 발급). 기존 `RESEND_API_KEY` 등은 이미 설정돼 있어 추가
   작업이 필요 없다.
3. `backend` 서비스를 재배포한다(`backend/railway.json`의 `buildCommand`
   변경 때문에 Playwright 브라우저 설치가 새로 필요 — 첫 배포는 평소보다
   오래 걸릴 수 있다). Railway MCP의 `deploy` 도구에 `path: "backend/"`를
   명시해서 호출하고, 배포 후 `list_deployments`로 상태가 `SUCCESS`가 될
   때까지, `get_logs`로 `playwright install`이 실제로 성공했는지 반드시
   확인한다.
4. `frontend`는 API 응답 필드 이름이 그대로라 변경 없이 이미 호환되지만,
   가게 연결 화면 UI가 바뀌었으므로 `frontend` 서비스도 재배포한다
   (`path: "frontend/"`).

## 다음 단계 (이번 계획 범위 밖)

- 배민 주문내역/정산 실데이터 연동 (완전히 별도 다음 단계).
- 쿠팡이츠/요기요 실데이터 연동.
- 리뷰 답글 실제 자동 등록 (여전히 절대 금지).
- 한 배민 계정의 다중 매장 선택 UI.
- 결제/구독 → LLM+RAG 답글 (`CLAUDE.md`의 "방향 전환" 절 순서대로).
