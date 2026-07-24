# 리뷰닥터 벤치마크 MVP 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배달매장 3대 현장 문제(리뷰 답글/정산 차액/광고 순위)를 Mock 데이터로 시연하는 DB 설계 중심 프로토타입.

**Architecture:** FastAPI + SQLAlchemy 2.0 모델이 핵심 산출물. PostgreSQL(Docker Compose)에 Alembic으로 스키마 생성, seed 스크립트로 현실적 Mock 데이터 주입, Next.js 화면 3개가 REST API를 소비. 테스트는 SQLite in-memory로 실행(모델은 양쪽 호환 타입만 사용).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, psycopg, pytest, PostgreSQL 16(Docker), Next.js(App Router, TypeScript, Tailwind)

**Spec:** `docs/superpowers/specs/2026-07-25-review-doctor-mvp-design.md`

## Global Constraints

- 외부 API 호출 금지: 크롤링·자동입찰·LLM 없음. 데이터 출처는 seed, 답글 생성은 템플릿 조회
- 금액은 전부 원 단위 정수(Integer). 소수점 없음
- 광고 스냅샷은 캠페인당 10분 간격 30개. `/api/ads/refresh`는 mock 시간을 10분 전진
- 추천 규칙은 단 하나: `my_rank > target_rank`이고 대기 추천 없으면 `suggested_cpc = competitor_est_cpc + 50`
- 상태 문자열 고정값: 리뷰 `unanswered|answered`, 정산 `scheduled|paid`, 캠페인 `active|paused`, 추천 `pending|applied|dismissed`, 별점대 `low(1–2)|mid(3)|high(4–5)`, 공제 타입 `platform_commission|payment_fee|delivery_fee|ad_fee|discount_support`
- 플랫폼 코드 고정값: `baemin|coupang_eats|yogiyo`
- 프론트 E2E 테스트 없음. 백엔드는 pytest 정합성 테스트 중심
- seed 정합성 불변식: `settlements.net_payout = Σ(소속 주문 item_amount+delivery_tip) − Σ(소속 주문 공제)`
- 기준 시각(seed): `2026-07-25 09:00` (naive datetime, KST 가정)
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: 백엔드 스캐폴드 + Docker Compose + 헬스체크

**Files:**
- Create: `docker-compose.yml`
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`, `backend/app/db.py`, `backend/app/main.py`
- Create: `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_health.py`
- Create: `backend/.gitignore`

**Interfaces:**
- Produces: `app.db.Base`(DeclarativeBase), `app.db.get_db()`(FastAPI 의존성, Session yield), `app.db.DATABASE_URL`, `app.main.app`(FastAPI 인스턴스), pytest fixture `db_session`(SQLite in-memory Session), `client`(TestClient, get_db 오버라이드)

- [ ] **Step 1: 프로젝트 파일 작성**

`docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: reviewdoctor
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

`backend/requirements.txt`:
```
fastapi
uvicorn[standard]
sqlalchemy
psycopg[binary]
alembic
pydantic
pytest
httpx
```

`backend/.gitignore`:
```
__pycache__/
.venv/
.pytest_cache/
```

`backend/app/db.py`:
```python
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/reviewdoctor"
)


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

`backend/app/main.py`:
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Review Doctor MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}
```

`backend/tests/conftest.py`:
```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
```

`backend/tests/test_health.py`:
```python
def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
```

`backend/app/__init__.py`, `backend/tests/__init__.py`: 빈 파일.

- [ ] **Step 2: 가상환경 생성 및 의존성 설치**

Run: `cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

- [ ] **Step 3: 테스트 실행**

Run: `cd backend && .venv/bin/python -m pytest tests/test_health.py -v`
Expected: PASS 1건

- [ ] **Step 4: DB 컨테이너 기동 확인**

Run: `docker compose up -d db && docker compose ps`
Expected: db 서비스 running

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml backend/
git commit -m "feat: 백엔드 스캐폴드 + Docker Compose + 헬스체크"
```

---

### Task 2: 공통 기반 모델 (owners/stores/platforms/store_platforms/mock_clock)

**Files:**
- Create: `backend/app/models/__init__.py`, `backend/app/models/core.py`
- Test: `backend/tests/test_models_core.py`

**Interfaces:**
- Consumes: `app.db.Base`
- Produces: `Owner(id,name,phone)`, `Store(id,owner_id,name,address)`, `Platform(id,code,name,default_commission_rate: float)`, `StorePlatform(id,store_id,platform_id,platform_store_name)` + 관계 `Store.owner`, `Store.store_platforms`, `StorePlatform.store`, `StorePlatform.platform`, `MockClock(id,mock_now: datetime)` — id는 항상 1

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_models_core.py`:
```python
from datetime import datetime

from app.models import MockClock, Owner, Platform, Store, StorePlatform


def test_store_platform_relationships(db_session):
    owner = Owner(name="김사장", phone="010-0000-0000")
    store = Store(owner=owner, name="우리치킨 1호점", address="서울시 어딘가 1")
    platform = Platform(code="baemin", name="배달의민족", default_commission_rate=0.068)
    sp = StorePlatform(store=store, platform=platform, platform_store_name="우리치킨-강남")
    db_session.add_all([owner, store, platform, sp])
    db_session.flush()

    assert sp.store.owner.name == "김사장"
    assert sp.platform.code == "baemin"
    assert store.store_platforms == [sp]


def test_mock_clock_row(db_session):
    db_session.add(MockClock(id=1, mock_now=datetime(2026, 7, 25, 9, 0)))
    db_session.flush()
    clock = db_session.get(MockClock, 1)
    assert clock.mock_now == datetime(2026, 7, 25, 9, 0)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_core.py -v`
Expected: FAIL (`ImportError: cannot import name 'Owner'`)

- [ ] **Step 3: 모델 구현**

`backend/app/models/core.py`:
```python
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(String(20))

    stores: Mapped[list["Store"]] = relationship(back_populates="owner")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(200))

    owner: Mapped[Owner] = relationship(back_populates="stores")
    store_platforms: Mapped[list["StorePlatform"]] = relationship(back_populates="store")


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    default_commission_rate: Mapped[float]


class StorePlatform(Base):
    __tablename__ = "store_platforms"
    __table_args__ = (UniqueConstraint("store_id", "platform_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    platform_store_name: Mapped[str] = mapped_column(String(100))

    store: Mapped[Store] = relationship(back_populates="store_platforms")
    platform: Mapped[Platform] = relationship()


class MockClock(Base):
    __tablename__ = "mock_clock"

    id: Mapped[int] = mapped_column(primary_key=True)
    mock_now: Mapped[datetime]
```

`backend/app/models/__init__.py`:
```python
from app.models.core import MockClock, Owner, Platform, Store, StorePlatform

__all__ = ["MockClock", "Owner", "Platform", "Store", "StorePlatform"]
```

`backend/tests/conftest.py`의 `db_session` fixture 상단에 모델 등록 import 추가:
```python
from app import models  # noqa: F401  (Base.metadata에 전체 테이블 등록)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_core.py -v`
Expected: PASS 2건

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/ backend/tests/
git commit -m "feat: 공통 기반 모델 5종 (owners/stores/platforms/store_platforms/mock_clock)"
```

---

### Task 3: 리뷰 도메인 모델 (reviews/reply_styles/reply_templates/review_replies)

**Files:**
- Create: `backend/app/models/reviews.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_models_reviews.py`

**Interfaces:**
- Consumes: `StorePlatform`
- Produces: `Review(id,store_platform_id,rating,content,reviewer_name,has_photo,status,created_at)` + 관계 `Review.reply`(1:1, None 가능), `ReplyStyle(id,name,description)`, `ReplyTemplate(id,style_id,rating_band,template_text)` — (style_id, rating_band) unique, `ReviewReply(id,review_id unique,style_id,content,created_at)`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_models_reviews.py`:
```python
from datetime import datetime

from app.models import Owner, Platform, ReplyStyle, ReplyTemplate, Review, ReviewReply, Store, StorePlatform


def make_sp(db_session):
    owner = Owner(name="김사장", phone="010-0000-0000")
    store = Store(owner=owner, name="우리치킨 1호점", address="서울시 어딘가 1")
    platform = Platform(code="baemin", name="배달의민족", default_commission_rate=0.068)
    sp = StorePlatform(store=store, platform=platform, platform_store_name="우리치킨-강남")
    db_session.add(sp)
    db_session.flush()
    return sp


def test_review_defaults_and_reply(db_session):
    sp = make_sp(db_session)
    review = Review(
        store_platform_id=sp.id,
        rating=5,
        content="맛있어요",
        reviewer_name="먹보",
        has_photo=False,
        created_at=datetime(2026, 7, 20, 18, 0),
    )
    db_session.add(review)
    db_session.flush()
    assert review.status == "unanswered"
    assert review.reply is None

    style = ReplyStyle(name="친근함", description="따뜻하고 다정한 말투")
    db_session.add(style)
    db_session.flush()
    reply = ReviewReply(
        review_id=review.id, style_id=style.id, content="감사합니다!",
        created_at=datetime(2026, 7, 21, 9, 0),
    )
    db_session.add(reply)
    db_session.flush()
    assert review.reply.content == "감사합니다!"


def test_template_band(db_session):
    style = ReplyStyle(name="정중함", description="격식 있는 말투")
    db_session.add(style)
    db_session.flush()
    tpl = ReplyTemplate(style_id=style.id, rating_band="high", template_text="{reviewer_name}님 감사합니다.")
    db_session.add(tpl)
    db_session.flush()
    assert tpl.rating_band == "high"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_reviews.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 모델 구현**

`backend/app/models/reviews.py`:
```python
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    rating: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    reviewer_name: Mapped[str] = mapped_column(String(50))
    has_photo: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="unanswered")
    created_at: Mapped[datetime]

    store_platform: Mapped["StorePlatform"] = relationship()
    reply: Mapped["ReviewReply | None"] = relationship(back_populates="review")


class ReplyStyle(Base):
    __tablename__ = "reply_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200))


class ReplyTemplate(Base):
    __tablename__ = "reply_templates"
    __table_args__ = (UniqueConstraint("style_id", "rating_band"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    style_id: Mapped[int] = mapped_column(ForeignKey("reply_styles.id"))
    rating_band: Mapped[str] = mapped_column(String(10))  # low | mid | high
    template_text: Mapped[str] = mapped_column(Text)


class ReviewReply(Base):
    __tablename__ = "review_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), unique=True)
    style_id: Mapped[int] = mapped_column(ForeignKey("reply_styles.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]

    review: Mapped[Review] = relationship(back_populates="reply")


from app.models.core import StorePlatform  # noqa: E402  (관계 타입 해석용)
```

`backend/app/models/__init__.py`에 추가:
```python
from app.models.reviews import ReplyStyle, ReplyTemplate, Review, ReviewReply
```
그리고 `__all__`에 `"Review", "ReplyStyle", "ReplyTemplate", "ReviewReply"` 추가.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_reviews.py -v`
Expected: PASS 2건

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/ backend/tests/test_models_reviews.py
git commit -m "feat: 리뷰 도메인 모델 4종"
```

---

### Task 4: 정산 도메인 모델 (orders/order_deductions/settlements)

**Files:**
- Create: `backend/app/models/settlements.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_models_settlements.py`

**Interfaces:**
- Consumes: `StorePlatform`
- Produces: `Settlement(id,store_platform_id,period_start: date,period_end: date,payout_date: date,total_gross,total_deductions,net_payout,status)`, `Order(id,store_platform_id,settlement_id nullable,order_no,ordered_at,item_amount,delivery_tip,status)` + 관계 `Order.deductions`, `Settlement.orders`, `OrderDeduction(id,order_id,type,amount)`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_models_settlements.py`:
```python
from datetime import date, datetime

from app.models import Order, OrderDeduction, Settlement
from tests.test_models_reviews import make_sp


def test_order_deductions_and_settlement_link(db_session):
    sp = make_sp(db_session)
    settlement = Settlement(
        store_platform_id=sp.id,
        period_start=date(2026, 7, 13), period_end=date(2026, 7, 19),
        payout_date=date(2026, 7, 22),
        total_gross=20000, total_deductions=5000, net_payout=15000,
        status="paid",
    )
    db_session.add(settlement)
    db_session.flush()

    order = Order(
        store_platform_id=sp.id, settlement_id=settlement.id,
        order_no="B20260713-001", ordered_at=datetime(2026, 7, 13, 18, 30),
        item_amount=18000, delivery_tip=2000, status="completed",
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        OrderDeduction(order_id=order.id, type="platform_commission", amount=1224),
        OrderDeduction(order_id=order.id, type="delivery_fee", amount=3300),
    ])
    db_session.flush()

    assert len(order.deductions) == 2
    assert settlement.orders[0].order_no == "B20260713-001"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_settlements.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 모델 구현**

`backend/app/models/settlements.py`:
```python
from datetime import date, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    payout_date: Mapped[date]
    total_gross: Mapped[int]
    total_deductions: Mapped[int]
    net_payout: Mapped[int]
    status: Mapped[str] = mapped_column(String(20))  # scheduled | paid

    orders: Mapped[list["Order"]] = relationship(back_populates="settlement")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("settlements.id"))
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    ordered_at: Mapped[datetime]
    item_amount: Mapped[int]
    delivery_tip: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="completed")

    settlement: Mapped[Settlement | None] = relationship(back_populates="orders")
    deductions: Mapped[list["OrderDeduction"]] = relationship(back_populates="order")


class OrderDeduction(Base):
    __tablename__ = "order_deductions"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="deductions")
```

`backend/app/models/__init__.py`에 추가:
```python
from app.models.settlements import Order, OrderDeduction, Settlement
```
그리고 `__all__`에 `"Order", "OrderDeduction", "Settlement"` 추가.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_settlements.py -v`
Expected: PASS 1건

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/ backend/tests/test_models_settlements.py
git commit -m "feat: 정산 도메인 모델 3종"
```

---

### Task 5: 광고 도메인 모델 (ad_campaigns/ad_rank_snapshots/ad_recommendations/ad_bid_history)

**Files:**
- Create: `backend/app/models/ads.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_models_ads.py`

**Interfaces:**
- Consumes: `StorePlatform`
- Produces: `AdCampaign(id,store_platform_id,category,current_cpc,target_rank,status)`, `AdRankSnapshot(id,campaign_id,snapshot_at,my_rank,competitor_est_cpc)`, `AdRecommendation(id,campaign_id,snapshot_id,action_type,suggested_cpc,status,created_at)`, `AdBidHistory(id,campaign_id,recommendation_id nullable,old_cpc,new_cpc,applied_at)`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_models_ads.py`:
```python
from datetime import datetime

from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation
from tests.test_models_reviews import make_sp


def test_ad_domain_chain(db_session):
    sp = make_sp(db_session)
    campaign = AdCampaign(
        store_platform_id=sp.id, category="치킨", current_cpc=400, target_rank=3, status="active"
    )
    db_session.add(campaign)
    db_session.flush()

    snap = AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0),
        my_rank=5, competitor_est_cpc=650,
    )
    db_session.add(snap)
    db_session.flush()

    rec = AdRecommendation(
        campaign_id=campaign.id, snapshot_id=snap.id,
        action_type="raise_cpc", suggested_cpc=700, status="pending",
        created_at=datetime(2026, 7, 25, 9, 0),
    )
    db_session.add(rec)
    db_session.flush()

    hist = AdBidHistory(
        campaign_id=campaign.id, recommendation_id=rec.id,
        old_cpc=400, new_cpc=700, applied_at=datetime(2026, 7, 25, 9, 10),
    )
    db_session.add(hist)
    db_session.flush()

    assert hist.recommendation_id == rec.id
    assert rec.snapshot_id == snap.id
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_ads.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 모델 구현**

`backend/app/models/ads.py`:
```python
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    category: Mapped[str] = mapped_column(String(30))
    current_cpc: Mapped[int]
    target_rank: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused

    store_platform: Mapped["StorePlatform"] = relationship()


class AdRankSnapshot(Base):
    __tablename__ = "ad_rank_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    snapshot_at: Mapped[datetime]
    my_rank: Mapped[int]
    competitor_est_cpc: Mapped[int]


class AdRecommendation(Base):
    __tablename__ = "ad_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ad_rank_snapshots.id"))
    action_type: Mapped[str] = mapped_column(String(20))  # raise_cpc | keep | lower_cpc
    suggested_cpc: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime]


class AdBidHistory(Base):
    __tablename__ = "ad_bid_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("ad_recommendations.id"))
    old_cpc: Mapped[int]
    new_cpc: Mapped[int]
    applied_at: Mapped[datetime]


from app.models.core import StorePlatform  # noqa: E402
```

`backend/app/models/__init__.py`에 추가:
```python
from app.models.ads import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation
```
그리고 `__all__`에 `"AdBidHistory", "AdCampaign", "AdRankSnapshot", "AdRecommendation"` 추가.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_ads.py -v`
Expected: PASS 1건

- [ ] **Step 5: 전체 테스트 회귀 확인 + Commit**

Run: `cd backend && .venv/bin/python -m pytest -v` → 전체 PASS 확인 후:
```bash
git add backend/app/models/ backend/tests/test_models_ads.py
git commit -m "feat: 광고 도메인 모델 4종 — 스키마 16개 테이블 완성"
```

---

### Task 6: Alembic 초기 마이그레이션 (PostgreSQL 스키마 생성)

**Files:**
- Create: `backend/alembic.ini`, `backend/alembic/` (alembic init 산출물)
- Modify: `backend/alembic/env.py`

**Interfaces:**
- Consumes: `app.db.Base.metadata`, `app.db.DATABASE_URL`, 전체 모델
- Produces: PostgreSQL에 16개 테이블. 이후 seed(Task 7)가 이 테이블에 insert

- [ ] **Step 1: Alembic 초기화**

Run: `cd backend && .venv/bin/alembic init alembic`
확인: `alembic.ini`의 `prepend_sys_path = .` 존재 (기본 템플릿에 포함)

- [ ] **Step 2: env.py 수정**

`backend/alembic/env.py`에서 `target_metadata = None` 부분을 다음으로 교체:
```python
from app.db import DATABASE_URL, Base
from app import models  # noqa: F401

config.set_main_option("sqlalchemy.url", DATABASE_URL)
target_metadata = Base.metadata
```

- [ ] **Step 3: 마이그레이션 생성 및 적용**

Run:
```bash
docker compose up -d db
cd backend && .venv/bin/alembic revision --autogenerate -m "initial schema"
.venv/bin/alembic upgrade head
```
Expected: 마이그레이션 파일 1개 생성, upgrade 성공

- [ ] **Step 4: 테이블 검증**

Run: `docker compose exec db psql -U postgres -d reviewdoctor -c "\dt"`
Expected: 16개 테이블 + `alembic_version` 표시

- [ ] **Step 5: Commit**

```bash
git add backend/alembic.ini backend/alembic/
git commit -m "feat: Alembic 초기 마이그레이션 — 16개 테이블"
```

---

### Task 7: Seed 스크립트 + 정합성 테스트

**Files:**
- Create: `backend/app/seed/__init__.py`, `backend/app/seed/run.py`
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: 전체 모델
- Produces: `app.seed.run.seed_all(session)` — 전체 삭제 후 재생성(멱등), `BASE_NOW = datetime(2026, 7, 25, 9, 0)`. CLI: `python -m app.seed.run`

- [ ] **Step 1: 실패하는 정합성 테스트 작성**

`backend/tests/test_seed.py`:
```python
from sqlalchemy import func, select

from app.models import (
    AdCampaign, AdRankSnapshot, MockClock, Order, OrderDeduction,
    ReplyTemplate, Review, Settlement, StorePlatform,
)
from app.seed.run import BASE_NOW, seed_all


def test_seed_settlement_invariant(db_session):
    seed_all(db_session)
    for settlement in db_session.scalars(select(Settlement)).all():
        orders = settlement.orders
        gross = sum(o.item_amount + o.delivery_tip for o in orders)
        ded = db_session.scalar(
            select(func.coalesce(func.sum(OrderDeduction.amount), 0))
            .join(Order, OrderDeduction.order_id == Order.id)
            .where(Order.settlement_id == settlement.id)
        )
        assert settlement.total_gross == gross
        assert settlement.total_deductions == ded
        assert settlement.net_payout == gross - ded


def test_seed_volumes(db_session):
    seed_all(db_session)
    assert db_session.scalar(select(func.count(StorePlatform.id))) == 4
    assert db_session.scalar(select(func.count(Review.id))) == 40
    assert db_session.scalar(select(func.count(ReplyTemplate.id))) == 9
    assert db_session.scalar(select(func.count(AdCampaign.id))) == 2
    assert db_session.scalar(select(func.count(AdRankSnapshot.id))) == 60  # 캠페인당 30
    order_count = db_session.scalar(select(func.count(Order.id)))
    assert 300 <= order_count <= 500
    assert db_session.get(MockClock, 1).mock_now == BASE_NOW


def test_seed_rank_slide_exists(db_session):
    seed_all(db_session)
    campaign = db_session.scalars(select(AdCampaign).where(AdCampaign.target_rank == 3)).first()
    ranks = db_session.scalars(
        select(AdRankSnapshot.my_rank)
        .where(AdRankSnapshot.campaign_id == campaign.id)
        .order_by(AdRankSnapshot.snapshot_at)
    ).all()
    assert ranks[0] == 3 and max(ranks) == 7  # 3위→7위 밀림 구간
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_seed.py -v`
Expected: FAIL (`ModuleNotFoundError: app.seed`)

- [ ] **Step 3: seed 구현**

`backend/app/seed/__init__.py`: 빈 파일.

`backend/app/seed/run.py`:
```python
import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import (
    AdCampaign, AdRankSnapshot, MockClock, Order, OrderDeduction, Owner,
    Platform, ReplyStyle, ReplyTemplate, Review, ReviewReply, Settlement,
    Store, StorePlatform,
)

BASE_NOW = datetime(2026, 7, 25, 9, 0)

PLATFORMS = [
    ("baemin", "배달의민족", 0.068),
    ("coupang_eats", "쿠팡이츠", 0.098),
    ("yogiyo", "요기요", 0.125),
]

TEMPLATES = {
    "친근함": {
        "high": "{reviewer_name}님~ 맛있게 드셨다니 저희가 더 행복해요! 다음에도 따끈하게 준비해둘게요 :)",
        "mid": "{reviewer_name}님, 솔직한 후기 감사해요! 다음엔 더 만족하실 수 있게 신경 쓸게요~",
        "low": "{reviewer_name}님, 불편을 드려 정말 죄송해요ㅠㅠ 말씀 주신 부분 바로 개선하겠습니다. 한 번만 더 기회 주세요!",
    },
    "장난꾸러기": {
        "high": "{reviewer_name}님!! 별 다섯 개 감사링~ 사장님 오늘 어깨 승천했습니다ㅋㅋ 또 오세용!",
        "mid": "{reviewer_name}님 아쉬운 부분이 있었군요! 사장님이 주방에 특훈 지시했습니다. 다음엔 꼭 만족시켜드릴게요!",
        "low": "{reviewer_name}님... 사장님 지금 반성의 정자세 중입니다. 죄송합니다! 다음엔 실망 안 시켜드릴게요!",
    },
    "정중함": {
        "high": "{reviewer_name}님, 소중한 리뷰 감사드립니다. 앞으로도 변함없는 맛과 서비스로 보답하겠습니다.",
        "mid": "{reviewer_name}님, 귀한 의견 감사드립니다. 말씀하신 부분을 검토하여 개선하겠습니다.",
        "low": "{reviewer_name}님, 기대에 미치지 못해 진심으로 사과드립니다. 지적해주신 사항은 즉시 개선하겠습니다.",
    },
}

REVIEW_SAMPLES = {
    "high": ["진짜 맛있어요 재주문 의사 100%", "바삭하고 양도 많아요. 최고!", "배달도 빠르고 친절해요"],
    "mid": ["맛은 있는데 배달이 좀 늦었어요", "무난해요. 가끔 시켜먹기 좋아요"],
    "low": ["식어서 왔어요. 실망입니다", "주문한 거랑 다른 게 왔어요"],
}

REVIEWERS = ["먹보", "치킨러버", "동네주민", "야식왕", "리뷰요정", "단골손님", "익명", "맛잘알"]


def band_of(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


def deductions_for(platform_code: str, item_amount: int, delivery_tip: int, rng: random.Random):
    gross = item_amount + delivery_tip
    if platform_code == "baemin":
        d = [("platform_commission", round(item_amount * 0.068)),
             ("payment_fee", round(gross * 0.03)),
             ("delivery_fee", 3300)]
    elif platform_code == "coupang_eats":
        d = [("platform_commission", round(item_amount * 0.098)),
             ("payment_fee", round(gross * 0.03)),
             ("delivery_fee", 2900)]
    else:  # yogiyo
        d = [("platform_commission", round(item_amount * 0.125)),
             ("payment_fee", round(gross * 0.03))]
    if rng.random() < 0.10:
        d.append(("ad_fee", rng.randrange(200, 601, 50)))
    return d


def seed_all(session: Session) -> None:
    rng = random.Random(42)

    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())

    owner = Owner(name="김사장", phone="010-1234-5678")
    store1 = Store(owner=owner, name="우리치킨 1호점", address="서울시 관악구 1")
    store2 = Store(owner=owner, name="우리치킨 2호점", address="서울시 동작구 2")
    platforms = {c: Platform(code=c, name=n, default_commission_rate=r) for c, n, r in PLATFORMS}
    session.add_all([owner, store1, store2, *platforms.values()])
    session.flush()

    sps = [
        StorePlatform(store=store1, platform=platforms["baemin"], platform_store_name="우리치킨-관악점"),
        StorePlatform(store=store1, platform=platforms["coupang_eats"], platform_store_name="우리치킨 관악"),
        StorePlatform(store=store1, platform=platforms["yogiyo"], platform_store_name="우리치킨(관악)"),
        StorePlatform(store=store2, platform=platforms["baemin"], platform_store_name="우리치킨-동작점"),
    ]
    session.add_all(sps)
    session.flush()

    # ---- 주문 60일치 ----
    orders_by_sp: dict[int, list[Order]] = {sp.id: [] for sp in sps}
    seq = 0
    for day_offset in range(60, 0, -1):
        day = BASE_NOW.date() - timedelta(days=day_offset)
        for sp in sps:
            for _ in range(rng.randint(1, 3)):
                seq += 1
                ordered_at = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=rng.randint(11, 21), minutes=rng.randint(0, 59)
                )
                order = Order(
                    store_platform_id=sp.id,
                    order_no=f"{sp.platform.code[:2].upper()}{day.strftime('%Y%m%d')}-{seq:04d}",
                    ordered_at=ordered_at,
                    item_amount=rng.randrange(15000, 36000, 1000),
                    delivery_tip=rng.choice([0, 1000, 2000, 3000]),
                    status="completed",
                )
                session.add(order)
                session.flush()
                for dtype, amount in deductions_for(sp.platform.code, order.item_amount, order.delivery_tip, rng):
                    session.add(OrderDeduction(order_id=order.id, type=dtype, amount=amount))
                orders_by_sp[sp.id].append(order)

    # ---- 주 단위 정산 (월~일, 입금 = 종료 +3일) ----
    for sp in sps:
        by_week: dict[date, list[Order]] = {}
        for order in orders_by_sp[sp.id]:
            monday = order.ordered_at.date() - timedelta(days=order.ordered_at.weekday())
            by_week.setdefault(monday, []).append(order)
        for monday, orders in sorted(by_week.items()):
            gross = sum(o.item_amount + o.delivery_tip for o in orders)
            ded = sum(d.amount for o in orders for d in o.deductions)
            payout = monday + timedelta(days=9)  # 일요일 종료 +3일 = 다음주 수요일
            settlement = Settlement(
                store_platform_id=sp.id,
                period_start=monday, period_end=monday + timedelta(days=6),
                payout_date=payout,
                total_gross=gross, total_deductions=ded, net_payout=gross - ded,
                status="paid" if payout < BASE_NOW.date() else "scheduled",
            )
            session.add(settlement)
            session.flush()
            for o in orders:
                o.settlement_id = settlement.id

    # ---- 답글 스타일/템플릿 ----
    styles = {}
    descs = {"친근함": "따뜻하고 다정한 말투", "장난꾸러기": "유쾌하고 장난스러운 말투", "정중함": "격식 있는 말투"}
    for name, bands in TEMPLATES.items():
        style = ReplyStyle(name=name, description=descs[name])
        session.add(style)
        session.flush()
        styles[name] = style
        for rating_band, text in bands.items():
            session.add(ReplyTemplate(style_id=style.id, rating_band=rating_band, template_text=text))

    # ---- 리뷰 40건, 절반 답글 완료 ----
    rating_pool = [5] * 22 + [4] * 8 + [3] * 4 + [2] * 3 + [1] * 3
    rng.shuffle(rating_pool)
    for i, rating in enumerate(rating_pool):
        b = band_of(rating)
        reviewer = rng.choice(REVIEWERS)
        review = Review(
            store_platform_id=rng.choice(sps).id,
            rating=rating,
            content=rng.choice(REVIEW_SAMPLES[b]),
            reviewer_name=reviewer,
            has_photo=rng.random() < 0.3,
            status="answered" if i < 20 else "unanswered",
            created_at=BASE_NOW - timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 12)),
        )
        session.add(review)
        session.flush()
        if review.status == "answered":
            style = rng.choice(list(styles.values()))
            text = TEMPLATES[style.name][b].replace("{reviewer_name}", reviewer)
            session.add(ReviewReply(
                review_id=review.id, style_id=style.id, content=text,
                created_at=review.created_at + timedelta(hours=rng.randint(1, 24)),
            ))

    # ---- 광고: 캠페인 2개, 10분 간격 스냅샷 30개 ----
    c1 = AdCampaign(store_platform_id=sps[0].id, category="치킨", current_cpc=400, target_rank=3, status="active")
    c2 = AdCampaign(store_platform_id=sps[1].id, category="치킨", current_cpc=300, target_rank=5, status="active")
    session.add_all([c1, c2])
    session.flush()
    for i in range(30):
        at = BASE_NOW + timedelta(minutes=10 * i)
        if i < 10:
            rank1, comp1 = 3, 390
        elif i < 15:
            rank1, comp1 = min(3 + (i - 9), 7), 650  # 3위→7위 밀림 구간
        else:
            rank1, comp1 = 7, 650
        session.add(AdRankSnapshot(campaign_id=c1.id, snapshot_at=at, my_rank=rank1, competitor_est_cpc=comp1))
        session.add(AdRankSnapshot(campaign_id=c2.id, snapshot_at=at, my_rank=rng.choice([1, 2]), competitor_est_cpc=280))

    session.add(MockClock(id=1, mock_now=BASE_NOW))
    session.commit()


if __name__ == "__main__":
    Base.metadata.create_all(engine)  # alembic 미적용 환경 대비 no-op 안전장치
    with SessionLocal() as session:
        seed_all(session)
    print("seed 완료")
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_seed.py -v`
Expected: PASS 3건

- [ ] **Step 5: 실제 PostgreSQL에 seed 주입**

Run: `cd backend && .venv/bin/python -m app.seed.run`
확인: `docker compose exec db psql -U postgres -d reviewdoctor -c "select count(*) from orders;"` → 300~500

- [ ] **Step 6: Commit**

```bash
git add backend/app/seed/ backend/tests/test_seed.py
git commit -m "feat: seed 스크립트 — 60일 주문/주간 정산/리뷰 40건/광고 스냅샷, 정합성 테스트"
```

---

### Task 8: 리뷰 API (목록/스타일/초안/저장)

**Files:**
- Create: `backend/app/routers/__init__.py`, `backend/app/routers/reviews.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_reviews.py`

**Interfaces:**
- Consumes: 리뷰 모델, `get_db`
- Produces:
  - `GET /api/reply-styles` → `[{id, name, description}]`
  - `GET /api/reviews?status=&store_platform_id=` → `[{id, store_name, platform_name, rating, content, reviewer_name, has_photo, status, created_at, reply: {content, style_id} | null}]` (created_at 내림차순)
  - `POST /api/reviews/{id}/reply/draft` body `{style_id}` → `{content, style_id}` (저장 안 함)
  - `POST /api/reviews/{id}/reply` body `{style_id, content}` → 저장 + 리뷰 status `answered`. 이미 답글 있으면 409

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_api_reviews.py`:
```python
from datetime import datetime

from app.models import ReplyStyle, ReplyTemplate, Review
from tests.test_models_reviews import make_sp


def setup_review(db_session, rating=5):
    sp = make_sp(db_session)
    style = ReplyStyle(name="친근함", description="따뜻한 말투")
    db_session.add(style)
    db_session.flush()
    db_session.add(ReplyTemplate(
        style_id=style.id, rating_band="high",
        template_text="{reviewer_name}님 감사해요!",
    ))
    review = Review(
        store_platform_id=sp.id, rating=rating, content="맛있어요",
        reviewer_name="먹보", has_photo=False, created_at=datetime(2026, 7, 20, 18, 0),
    )
    db_session.add(review)
    db_session.commit()
    return review, style


def test_list_reviews(client, db_session):
    review, _ = setup_review(db_session)
    res = client.get("/api/reviews")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["platform_name"] == "배달의민족"
    assert body[0]["reply"] is None


def test_draft_fills_template(client, db_session):
    review, style = setup_review(db_session)
    res = client.post(f"/api/reviews/{review.id}/reply/draft", json={"style_id": style.id})
    assert res.status_code == 200
    assert res.json()["content"] == "먹보님 감사해요!"


def test_save_reply_transitions_status(client, db_session):
    review, style = setup_review(db_session)
    res = client.post(
        f"/api/reviews/{review.id}/reply",
        json={"style_id": style.id, "content": "먹보님 감사해요! 또 오세요."},
    )
    assert res.status_code == 200
    listed = client.get("/api/reviews?status=answered").json()
    assert len(listed) == 1
    assert listed[0]["reply"]["content"] == "먹보님 감사해요! 또 오세요."

    dup = client.post(
        f"/api/reviews/{review.id}/reply",
        json={"style_id": style.id, "content": "중복"},
    )
    assert dup.status_code == 409
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_reviews.py -v`
Expected: FAIL (404 — 라우터 없음)

- [ ] **Step 3: 라우터 구현**

`backend/app/routers/__init__.py`: 빈 파일.

`backend/app/routers/reviews.py`:
```python
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ReplyStyle, ReplyTemplate, Review, ReviewReply

router = APIRouter(prefix="/api", tags=["reviews"])


def band_of(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


class DraftRequest(BaseModel):
    style_id: int


class ReplyRequest(BaseModel):
    style_id: int
    content: str


@router.get("/reply-styles")
def list_styles(db: Session = Depends(get_db)):
    styles = db.scalars(select(ReplyStyle).order_by(ReplyStyle.id)).all()
    return [{"id": s.id, "name": s.name, "description": s.description} for s in styles]


@router.get("/reviews")
def list_reviews(
    status: str | None = None,
    store_platform_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Review).order_by(Review.created_at.desc())
    if status:
        stmt = stmt.where(Review.status == status)
    if store_platform_id:
        stmt = stmt.where(Review.store_platform_id == store_platform_id)
    reviews = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "store_name": r.store_platform.store.name,
            "platform_name": r.store_platform.platform.name,
            "rating": r.rating,
            "content": r.content,
            "reviewer_name": r.reviewer_name,
            "has_photo": r.has_photo,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "reply": {"content": r.reply.content, "style_id": r.reply.style_id} if r.reply else None,
        }
        for r in reviews
    ]


@router.post("/reviews/{review_id}/reply/draft")
def draft_reply(review_id: int, body: DraftRequest, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "리뷰 없음")
    template = db.scalar(
        select(ReplyTemplate).where(
            ReplyTemplate.style_id == body.style_id,
            ReplyTemplate.rating_band == band_of(review.rating),
        )
    )
    if template is None:
        raise HTTPException(404, "해당 스타일/별점대 템플릿 없음")
    content = template.template_text.replace("{reviewer_name}", review.reviewer_name)
    return {"content": content, "style_id": body.style_id}


@router.post("/reviews/{review_id}/reply")
def save_reply(review_id: int, body: ReplyRequest, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "리뷰 없음")
    if review.reply is not None:
        raise HTTPException(409, "이미 답글이 존재함")
    reply = ReviewReply(
        review_id=review.id, style_id=body.style_id,
        content=body.content, created_at=datetime.now(),
    )
    review.status = "answered"
    db.add(reply)
    db.commit()
    return {"id": reply.id, "content": reply.content}
```

`backend/app/main.py`에 등록 (health 아래):
```python
from app.routers.reviews import router as reviews_router

app.include_router(reviews_router)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_reviews.py -v`
Expected: PASS 3건

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/ backend/app/main.py backend/tests/test_api_reviews.py
git commit -m "feat: 리뷰 API — 목록/스타일/템플릿 초안/답글 저장"
```

---

### Task 9: 정산 API (목록/차액 분해)

**Files:**
- Create: `backend/app/routers/settlements.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_settlements.py`

**Interfaces:**
- Consumes: 정산 모델, `get_db`
- Produces:
  - `GET /api/settlements?platform_code=&from_date=&to_date=` → `[{id, store_name, platform_name, period_start, period_end, payout_date, total_gross, total_deductions, net_payout, status}]` (period_start 내림차순)
  - `GET /api/settlements/{id}` → 위 필드 + `deductions_by_type: [{type, amount}]` + `orders: [{id, order_no, ordered_at, item_amount, delivery_tip, deduction_total}]`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_api_settlements.py`:
```python
from datetime import date, datetime

from app.models import Order, OrderDeduction, Settlement
from tests.test_models_reviews import make_sp


def setup_settlement(db_session):
    sp = make_sp(db_session)
    settlement = Settlement(
        store_platform_id=sp.id,
        period_start=date(2026, 7, 13), period_end=date(2026, 7, 19),
        payout_date=date(2026, 7, 22),
        total_gross=20000, total_deductions=4524, net_payout=15476,
        status="paid",
    )
    db_session.add(settlement)
    db_session.flush()
    order = Order(
        store_platform_id=sp.id, settlement_id=settlement.id,
        order_no="BA20260713-0001", ordered_at=datetime(2026, 7, 13, 18, 30),
        item_amount=18000, delivery_tip=2000, status="completed",
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        OrderDeduction(order_id=order.id, type="platform_commission", amount=1224),
        OrderDeduction(order_id=order.id, type="delivery_fee", amount=3300),
    ])
    db_session.commit()
    return settlement


def test_list_settlements_with_filter(client, db_session):
    setup_settlement(db_session)
    assert len(client.get("/api/settlements").json()) == 1
    assert len(client.get("/api/settlements?platform_code=baemin").json()) == 1
    assert len(client.get("/api/settlements?platform_code=yogiyo").json()) == 0
    assert len(client.get("/api/settlements?from_date=2026-07-14").json()) == 0


def test_settlement_detail_breakdown(client, db_session):
    settlement = setup_settlement(db_session)
    res = client.get(f"/api/settlements/{settlement.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["net_payout"] == 15476
    by_type = {d["type"]: d["amount"] for d in body["deductions_by_type"]}
    assert by_type == {"platform_commission": 1224, "delivery_fee": 3300}
    assert body["orders"][0]["deduction_total"] == 4524
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_settlements.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 라우터 구현**

`backend/app/routers/settlements.py`:
```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order, OrderDeduction, Platform, Settlement, StorePlatform

router = APIRouter(prefix="/api", tags=["settlements"])


def _row(s: Settlement, db: Session) -> dict:
    sp = db.get(StorePlatform, s.store_platform_id)
    return {
        "id": s.id,
        "store_name": sp.store.name,
        "platform_name": sp.platform.name,
        "period_start": s.period_start.isoformat(),
        "period_end": s.period_end.isoformat(),
        "payout_date": s.payout_date.isoformat(),
        "total_gross": s.total_gross,
        "total_deductions": s.total_deductions,
        "net_payout": s.net_payout,
        "status": s.status,
    }


@router.get("/settlements")
def list_settlements(
    platform_code: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Settlement).order_by(Settlement.period_start.desc())
    if platform_code:
        stmt = (
            stmt.join(StorePlatform, Settlement.store_platform_id == StorePlatform.id)
            .join(Platform, StorePlatform.platform_id == Platform.id)
            .where(Platform.code == platform_code)
        )
    if from_date:
        stmt = stmt.where(Settlement.period_start >= from_date)
    if to_date:
        stmt = stmt.where(Settlement.period_end <= to_date)
    return [_row(s, db) for s in db.scalars(stmt).all()]


@router.get("/settlements/{settlement_id}")
def settlement_detail(settlement_id: int, db: Session = Depends(get_db)):
    s = db.get(Settlement, settlement_id)
    if s is None:
        raise HTTPException(404, "정산 없음")
    by_type = db.execute(
        select(OrderDeduction.type, func.sum(OrderDeduction.amount))
        .join(Order, OrderDeduction.order_id == Order.id)
        .where(Order.settlement_id == s.id)
        .group_by(OrderDeduction.type)
    ).all()
    orders = [
        {
            "id": o.id,
            "order_no": o.order_no,
            "ordered_at": o.ordered_at.isoformat(),
            "item_amount": o.item_amount,
            "delivery_tip": o.delivery_tip,
            "deduction_total": sum(d.amount for d in o.deductions),
        }
        for o in s.orders
    ]
    return {**_row(s, db), "deductions_by_type": [{"type": t, "amount": a} for t, a in by_type], "orders": orders}
```

`backend/app/main.py`에 등록:
```python
from app.routers.settlements import router as settlements_router

app.include_router(settlements_router)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_settlements.py -v`
Expected: PASS 2건

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/settlements.py backend/app/main.py backend/tests/test_api_settlements.py
git commit -m "feat: 정산 API — 목록 필터 + 차액 분해 상세"
```

---

### Task 10: 광고 API (대시보드/refresh/추천 적용·무시)

**Files:**
- Create: `backend/app/routers/ads.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_ads.py`

**Interfaces:**
- Consumes: 광고 모델, `MockClock`, `get_db`
- Produces:
  - `GET /api/ad-campaigns` → `{mock_now, campaigns: [{id, store_name, platform_name, category, current_cpc, target_rank, my_rank, competitor_est_cpc, status, recommendation: {id, action_type, suggested_cpc} | null}]}` (my_rank 등은 `snapshot_at <= mock_now` 최신 스냅샷, 없으면 null)
  - `POST /api/ads/refresh` → mock_now 10분 전진 + 추천 규칙 실행 → `{mock_now}`
  - `POST /api/ad-recommendations/{id}/apply` → bid_history 기록 + current_cpc 갱신 + status `applied`. pending 아니면 409
  - `POST /api/ad-recommendations/{id}/dismiss` → status `dismissed`. pending 아니면 409

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_api_ads.py`:
```python
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation, MockClock
from tests.test_models_reviews import make_sp

T0 = datetime(2026, 7, 25, 9, 0)


def setup_campaign(db_session):
    sp = make_sp(db_session)
    campaign = AdCampaign(store_platform_id=sp.id, category="치킨", current_cpc=400, target_rank=3, status="active")
    db_session.add(campaign)
    db_session.flush()
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=T0, my_rank=3, competitor_est_cpc=390),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=T0 + timedelta(minutes=10), my_rank=5, competitor_est_cpc=650),
    ])
    db_session.add(MockClock(id=1, mock_now=T0))
    db_session.commit()
    return campaign


def test_dashboard_uses_visible_snapshot(client, db_session):
    setup_campaign(db_session)
    body = client.get("/api/ad-campaigns").json()
    assert body["mock_now"] == T0.isoformat()
    row = body["campaigns"][0]
    assert row["my_rank"] == 3  # 미래 스냅샷(5위)은 아직 안 보임
    assert row["recommendation"] is None


def test_refresh_advances_and_recommends(client, db_session):
    campaign = setup_campaign(db_session)
    res = client.post("/api/ads/refresh")
    assert res.json()["mock_now"] == (T0 + timedelta(minutes=10)).isoformat()

    rec = db_session.scalars(select(AdRecommendation)).one()
    assert rec.action_type == "raise_cpc"
    assert rec.suggested_cpc == 650 + 50
    assert rec.status == "pending"

    client.post("/api/ads/refresh")  # pending 존재 → 중복 생성 금지
    assert len(db_session.scalars(select(AdRecommendation)).all()) == 1


def test_apply_records_history(client, db_session):
    campaign = setup_campaign(db_session)
    client.post("/api/ads/refresh")
    rec = db_session.scalars(select(AdRecommendation)).one()

    res = client.post(f"/api/ad-recommendations/{rec.id}/apply")
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(AdCampaign, campaign.id).current_cpc == 700
    hist = db_session.scalars(select(AdBidHistory)).one()
    assert (hist.old_cpc, hist.new_cpc, hist.recommendation_id) == (400, 700, rec.id)

    assert client.post(f"/api/ad-recommendations/{rec.id}/apply").status_code == 409


def test_dismiss(client, db_session):
    setup_campaign(db_session)
    client.post("/api/ads/refresh")
    rec = db_session.scalars(select(AdRecommendation)).one()
    assert client.post(f"/api/ad-recommendations/{rec.id}/dismiss").status_code == 200
    db_session.expire_all()
    assert rec.status == "dismissed"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_ads.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 라우터 구현**

`backend/app/routers/ads.py`:
```python
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation, MockClock

router = APIRouter(prefix="/api", tags=["ads"])


def _clock(db: Session) -> MockClock:
    clock = db.get(MockClock, 1)
    if clock is None:
        raise HTTPException(500, "mock_clock 미초기화 — seed를 먼저 실행하세요")
    return clock


def _latest_snapshot(db: Session, campaign_id: int, mock_now) -> AdRankSnapshot | None:
    return db.scalar(
        select(AdRankSnapshot)
        .where(AdRankSnapshot.campaign_id == campaign_id, AdRankSnapshot.snapshot_at <= mock_now)
        .order_by(AdRankSnapshot.snapshot_at.desc())
        .limit(1)
    )


def _pending_rec(db: Session, campaign_id: int) -> AdRecommendation | None:
    return db.scalar(
        select(AdRecommendation).where(
            AdRecommendation.campaign_id == campaign_id, AdRecommendation.status == "pending"
        )
    )


@router.get("/ad-campaigns")
def dashboard(db: Session = Depends(get_db)):
    clock = _clock(db)
    campaigns = db.scalars(select(AdCampaign).order_by(AdCampaign.id)).all()
    rows = []
    for c in campaigns:
        snap = _latest_snapshot(db, c.id, clock.mock_now)
        rec = _pending_rec(db, c.id)
        rows.append({
            "id": c.id,
            "store_name": c.store_platform.store.name,
            "platform_name": c.store_platform.platform.name,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "target_rank": c.target_rank,
            "my_rank": snap.my_rank if snap else None,
            "competitor_est_cpc": snap.competitor_est_cpc if snap else None,
            "status": c.status,
            "recommendation": (
                {"id": rec.id, "action_type": rec.action_type, "suggested_cpc": rec.suggested_cpc}
                if rec else None
            ),
        })
    return {"mock_now": clock.mock_now.isoformat(), "campaigns": rows}


@router.post("/ads/refresh")
def refresh(db: Session = Depends(get_db)):
    clock = _clock(db)
    clock.mock_now = clock.mock_now + timedelta(minutes=10)
    for c in db.scalars(select(AdCampaign).where(AdCampaign.status == "active")).all():
        snap = _latest_snapshot(db, c.id, clock.mock_now)
        if snap and snap.my_rank > c.target_rank and _pending_rec(db, c.id) is None:
            db.add(AdRecommendation(
                campaign_id=c.id, snapshot_id=snap.id,
                action_type="raise_cpc", suggested_cpc=snap.competitor_est_cpc + 50,
                status="pending", created_at=clock.mock_now,
            ))
    db.commit()
    return {"mock_now": clock.mock_now.isoformat()}


@router.post("/ad-recommendations/{rec_id}/apply")
def apply_recommendation(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(AdRecommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "추천 없음")
    if rec.status != "pending":
        raise HTTPException(409, "대기 상태 추천만 적용 가능")
    campaign = db.get(AdCampaign, rec.campaign_id)
    clock = _clock(db)
    db.add(AdBidHistory(
        campaign_id=campaign.id, recommendation_id=rec.id,
        old_cpc=campaign.current_cpc, new_cpc=rec.suggested_cpc,
        applied_at=clock.mock_now,
    ))
    campaign.current_cpc = rec.suggested_cpc
    rec.status = "applied"
    db.commit()
    return {"current_cpc": campaign.current_cpc}


@router.post("/ad-recommendations/{rec_id}/dismiss")
def dismiss_recommendation(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(AdRecommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "추천 없음")
    if rec.status != "pending":
        raise HTTPException(409, "대기 상태 추천만 무시 가능")
    rec.status = "dismissed"
    db.commit()
    return {"status": rec.status}
```

`backend/app/main.py`에 등록:
```python
from app.routers.ads import router as ads_router

app.include_router(ads_router)
```

- [ ] **Step 4: 통과 확인 (전체 회귀 포함)**

Run: `cd backend && .venv/bin/python -m pytest -v`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/ads.py backend/app/main.py backend/tests/test_api_ads.py
git commit -m "feat: 광고 API — 대시보드/mock 시간 전진/추천 적용·무시"
```

---

### Task 11: Next.js 스캐폴드 + API 클라이언트 + 홈

**Files:**
- Create: `frontend/` (create-next-app 산출물)
- Create: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/page.tsx`

**Interfaces:**
- Consumes: 백엔드 `http://localhost:8000`
- Produces: `apiGet<T>(path)`, `apiPost<T>(path, body?)` — 이후 화면 3개가 사용. 환경변수 `NEXT_PUBLIC_API_URL`(기본 `http://localhost:8000`)

- [ ] **Step 1: 스캐폴드**

Run (저장소 루트): `npx create-next-app@latest frontend --typescript --app --tailwind --eslint --src-dir --import-alias "@/*" --use-npm`
(프롬프트가 나오면 전부 기본값)

- [ ] **Step 2: API 클라이언트 작성**

`frontend/src/lib/api.ts`:
```ts
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json();
}

export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
```

- [ ] **Step 3: 홈 화면 교체**

`frontend/src/app/page.tsx` 전체 교체:
```tsx
import Link from "next/link";

const menus = [
  { href: "/reviews", title: "리뷰 답글", desc: "쌓인 리뷰에 스타일 답글 달기" },
  { href: "/settlements", title: "정산 차액", desc: "주문 총액과 실입금액이 왜 다른지 분해" },
  { href: "/ads", title: "광고 순위 모니터링", desc: "CPC·순위·추천 액션 한눈에" },
];

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-bold">리뷰닥터 벤치마크 MVP</h1>
      <p className="mt-1 text-sm text-gray-500">배달매장 3대 현장 문제 — Mock 데이터 프로토타입</p>
      <div className="mt-6 grid gap-4">
        {menus.map((m) => (
          <Link key={m.href} href={m.href} className="rounded-lg border p-4 hover:bg-gray-50">
            <div className="font-semibold">{m.title}</div>
            <div className="text-sm text-gray-500">{m.desc}</div>
          </Link>
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: 구동 확인**

Run: `cd frontend && npm run dev` (백그라운드) 후 `curl -s http://localhost:3000 | grep "리뷰닥터"`
Expected: 문자열 매치

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: Next.js 스캐폴드 + API 클라이언트 + 홈 화면"
```

---

### Task 12: 리뷰 화면

**Files:**
- Create: `frontend/src/app/reviews/page.tsx`

**Interfaces:**
- Consumes: `GET /api/reviews`, `GET /api/reply-styles`, `POST /api/reviews/{id}/reply/draft`, `POST /api/reviews/{id}/reply`, `apiGet/apiPost/won`

- [ ] **Step 1: 화면 구현**

`frontend/src/app/reviews/page.tsx`:
```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";

type Reply = { content: string; style_id: number };
type Review = {
  id: number; store_name: string; platform_name: string; rating: number;
  content: string; reviewer_name: string; status: string; created_at: string;
  reply: Reply | null;
};
type Style = { id: number; name: string; description: string };

function ReviewCard({ review, styles, onSaved }: { review: Review; styles: Style[]; onSaved: () => void }) {
  const [styleId, setStyleId] = useState(styles[0]?.id ?? 0);
  const [draft, setDraft] = useState("");

  const generate = async () => {
    const res = await apiPost<{ content: string }>(`/api/reviews/${review.id}/reply/draft`, { style_id: styleId });
    setDraft(res.content);
  };
  const save = async () => {
    await apiPost(`/api/reviews/${review.id}/reply`, { style_id: styleId, content: draft });
    onSaved();
  };

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-amber-500">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium">{review.reviewer_name}</span>
        <span className="text-gray-400">{review.store_name} · {review.platform_name}</span>
      </div>
      <p className="mt-2">{review.content}</p>
      {review.reply ? (
        <div className="mt-3 rounded bg-gray-50 p-3 text-sm">
          <span className="font-medium">사장님 답글</span>
          <p className="mt-1">{review.reply.content}</p>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="flex gap-2">
            <select value={styleId} onChange={(e) => setStyleId(Number(e.target.value))} className="rounded border px-2 py-1 text-sm">
              {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <button onClick={generate} className="rounded bg-blue-600 px-3 py-1 text-sm text-white">답글 생성</button>
          </div>
          {draft && (
            <div className="space-y-2">
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} className="w-full rounded border p-2 text-sm" />
              <button onClick={save} className="rounded bg-green-600 px-3 py-1 text-sm text-white">저장</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [styles, setStyles] = useState<Style[]>([]);
  const [filter, setFilter] = useState<"all" | "unanswered" | "answered">("unanswered");

  const load = useCallback(async () => {
    const qs = filter === "all" ? "" : `?status=${filter}`;
    setReviews(await apiGet<Review[]>(`/api/reviews${qs}`));
  }, [filter]);

  useEffect(() => { apiGet<Style[]>("/api/reply-styles").then(setStyles); }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-xl font-bold">리뷰 답글</h1>
      <div className="mt-3 flex gap-2 text-sm">
        {(["unanswered", "answered", "all"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded px-3 py-1 ${filter === f ? "bg-black text-white" : "border"}`}>
            {f === "unanswered" ? "답글 대기" : f === "answered" ? "답글 완료" : "전체"}
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-3">
        {reviews.map((r) => <ReviewCard key={r.id} review={r} styles={styles} onSaved={load} />)}
        {reviews.length === 0 && <p className="text-sm text-gray-400">리뷰가 없습니다.</p>}
      </div>
    </main>
  );
}
```

- [ ] **Step 2: 수동 검증**

백엔드(`cd backend && .venv/bin/uvicorn app.main:app --reload`)와 프론트 dev 서버 기동 후 http://localhost:3000/reviews 접속.
확인: 답글 대기 리뷰 목록 → 스타일 선택 → [답글 생성] → 텍스트 수정 → [저장] → 답글 완료 필터에 나타남.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/reviews/
git commit -m "feat: 리뷰 답글 화면 — 스타일 선택/템플릿 초안/저장"
```

---

### Task 13: 정산 화면

**Files:**
- Create: `frontend/src/app/settlements/page.tsx`

**Interfaces:**
- Consumes: `GET /api/settlements`, `GET /api/settlements/{id}`, `won`

- [ ] **Step 1: 화면 구현**

`frontend/src/app/settlements/page.tsx`:
```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, won } from "@/lib/api";

type Row = {
  id: number; store_name: string; platform_name: string;
  period_start: string; period_end: string; payout_date: string;
  total_gross: number; total_deductions: number; net_payout: number; status: string;
};
type Detail = Row & {
  deductions_by_type: { type: string; amount: number }[];
  orders: { id: number; order_no: string; ordered_at: string; item_amount: number; delivery_tip: number; deduction_total: number }[];
};

const DEDUCTION_LABEL: Record<string, string> = {
  platform_commission: "중개수수료", payment_fee: "결제수수료",
  delivery_fee: "배달비", ad_fee: "광고비", discount_support: "할인지원",
};
const PLATFORM_OPTIONS = [
  { code: "", label: "전체 플랫폼" }, { code: "baemin", label: "배달의민족" },
  { code: "coupang_eats", label: "쿠팡이츠" }, { code: "yogiyo", label: "요기요" },
];

export default function SettlementsPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [platform, setPlatform] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);

  const load = useCallback(async () => {
    const qs = platform ? `?platform_code=${platform}` : "";
    setRows(await apiGet<Row[]>(`/api/settlements${qs}`));
    setDetail(null);
  }, [platform]);

  useEffect(() => { load(); }, [load]);

  return (
    <main className="mx-auto max-w-5xl p-8">
      <h1 className="text-xl font-bold">정산 차액 분해</h1>
      <select value={platform} onChange={(e) => setPlatform(e.target.value)} className="mt-3 rounded border px-2 py-1 text-sm">
        {PLATFORM_OPTIONS.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}
      </select>
      <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
        <table className="w-full text-sm">
          <thead><tr className="border-b text-left text-gray-500">
            <th className="py-2">기간</th><th>매장/플랫폼</th><th className="text-right">실입금</th><th>상태</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} onClick={async () => setDetail(await apiGet<Detail>(`/api/settlements/${r.id}`))}
                className={`cursor-pointer border-b hover:bg-gray-50 ${detail?.id === r.id ? "bg-blue-50" : ""}`}>
                <td className="py-2">{r.period_start} ~ {r.period_end}</td>
                <td>{r.store_name}<span className="text-gray-400"> · {r.platform_name}</span></td>
                <td className="text-right font-medium">{won(r.net_payout)}</td>
                <td>{r.status === "paid" ? "입금완료" : "입금예정"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {detail && (
          <div className="rounded-lg border p-4 text-sm">
            <h2 className="font-semibold">{detail.period_start} ~ {detail.period_end} · {detail.platform_name}</h2>
            <dl className="mt-3 space-y-1">
              <div className="flex justify-between"><dt>주문 총액</dt><dd className="font-medium">{won(detail.total_gross)}</dd></div>
              {detail.deductions_by_type.map((d) => (
                <div key={d.type} className="flex justify-between text-red-600">
                  <dt>− {DEDUCTION_LABEL[d.type] ?? d.type}</dt><dd>−{won(d.amount)}</dd>
                </div>
              ))}
              <div className="flex justify-between border-t pt-1 text-base font-bold">
                <dt>실입금액</dt><dd>{won(detail.net_payout)}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-gray-400">주문 {detail.orders.length}건 · 입금일 {detail.payout_date}</p>
          </div>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 2: 수동 검증**

http://localhost:3000/settlements 접속.
확인: 정산 목록 → 행 클릭 → 우측에 "주문 총액 → 공제 항목별 − → 실입금액" 분해 표시. 플랫폼 필터 변경 시 목록/공제 구성이 달라짐(요기요는 배달비 없음).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/settlements/
git commit -m "feat: 정산 화면 — 플랫폼 필터 + 차액 분해 패널"
```

---

### Task 14: 광고 순위 모니터링 화면

**Files:**
- Create: `frontend/src/app/ads/page.tsx`

**Interfaces:**
- Consumes: `GET /api/ad-campaigns`, `POST /api/ads/refresh`, `POST /api/ad-recommendations/{id}/apply`, `POST /api/ad-recommendations/{id}/dismiss`, `won`

- [ ] **Step 1: 화면 구현**

`frontend/src/app/ads/page.tsx`:
```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, won } from "@/lib/api";

type Rec = { id: number; action_type: string; suggested_cpc: number };
type Campaign = {
  id: number; store_name: string; platform_name: string; category: string;
  current_cpc: number; target_rank: number; my_rank: number | null;
  competitor_est_cpc: number | null; status: string; recommendation: Rec | null;
};

export default function AdsPage() {
  const [mockNow, setMockNow] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  const load = useCallback(async () => {
    const body = await apiGet<{ mock_now: string; campaigns: Campaign[] }>("/api/ad-campaigns");
    setMockNow(body.mock_now);
    setCampaigns(body.campaigns);
  }, []);

  useEffect(() => { load(); }, [load]);

  const refresh = async () => { await apiPost("/api/ads/refresh"); load(); };
  const act = async (recId: number, action: "apply" | "dismiss") => {
    await apiPost(`/api/ad-recommendations/${recId}/${action}`);
    load();
  };

  return (
    <main className="mx-auto max-w-5xl p-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">광고 순위 모니터링</h1>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-gray-500">기준 시각 {mockNow.replace("T", " ")}</span>
          <button onClick={refresh} className="rounded bg-black px-3 py-1 text-white">새로고침 (+10분)</button>
        </div>
      </div>
      <table className="mt-4 w-full text-sm">
        <thead><tr className="border-b text-left text-gray-500">
          <th className="py-2">매장/플랫폼</th><th>카테고리</th><th className="text-right">현재 CPC</th>
          <th className="text-center">목표 순위</th><th className="text-center">현재 순위</th>
          <th className="text-right">경쟁 예상 CPC</th><th>상태</th><th>추천 액션</th>
        </tr></thead>
        <tbody>
          {campaigns.map((c) => {
            const slipped = c.my_rank !== null && c.my_rank > c.target_rank;
            return (
              <tr key={c.id} className="border-b">
                <td className="py-2">{c.store_name}<span className="text-gray-400"> · {c.platform_name}</span></td>
                <td>{c.category}</td>
                <td className="text-right">{won(c.current_cpc)}</td>
                <td className="text-center">{c.target_rank}위</td>
                <td className={`text-center font-bold ${slipped ? "text-red-600" : "text-green-600"}`}>
                  {c.my_rank === null ? "—" : `${c.my_rank}위`}
                </td>
                <td className="text-right">{c.competitor_est_cpc === null ? "—" : won(c.competitor_est_cpc)}</td>
                <td>{slipped ? "순위 밀림" : c.status === "active" ? "정상" : "일시정지"}</td>
                <td>
                  {c.recommendation ? (
                    <div className="flex items-center gap-2">
                      <span>CPC {won(c.recommendation.suggested_cpc)}로 인상</span>
                      <button onClick={() => act(c.recommendation!.id, "apply")} className="rounded bg-blue-600 px-2 py-0.5 text-xs text-white">적용</button>
                      <button onClick={() => act(c.recommendation!.id, "dismiss")} className="rounded border px-2 py-0.5 text-xs">무시</button>
                    </div>
                  ) : <span className="text-gray-400">유지</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-gray-400">
        순위·경쟁 CPC는 seed된 10분 간격 스냅샷이며, 새로고침마다 mock 시간이 전진합니다. 실제 크롤링/자동입찰 없음.
      </p>
    </main>
  );
}
```

- [ ] **Step 2: 수동 검증**

http://localhost:3000/ads 접속.
확인: [새로고침]을 여러 번 누르면 시각이 10분씩 전진, 10회쯤부터 순위가 3→7위로 밀리고 "순위 밀림" + 추천(경쟁 CPC+50원)이 표시됨 → [적용] 시 현재 CPC 갱신, [무시] 시 추천 사라짐.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/ads/
git commit -m "feat: 광고 순위 모니터링 화면 — 7개 컬럼 + refresh + 추천 적용/무시"
```

---

### Task 15: README + 전체 검증

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: 전체

- [ ] **Step 1: README 작성**

`README.md` 전체 교체:
```markdown
# 리뷰닥터 벤치마크 MVP (review-doctor-mockup)

배달매장 사장의 3대 현장 문제를 Mock 데이터로 시연하는 **DB 설계 중심** 프로토타입.
리뷰 답글 노동 / 매출·입금 차액 / 광고 순위 밀림.

외부 API·크롤링·자동입찰·LLM 없음. 데이터는 seed, 답글은 템플릿, 순위는 시계열 스냅샷.

## 실행

```bash
# 1. DB
docker compose up -d db

# 2. 백엔드 (스키마 + seed + 서버)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed.run
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000

# 3. 프론트
cd frontend
npm install
npm run dev                               # http://localhost:3000
```

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -v
```

## 설계 문서

- 스펙: `docs/superpowers/specs/2026-07-25-review-doctor-mvp-design.md` (테이블 16개 ERD·범위 결정 기록 포함)
- 구현 계획: `docs/superpowers/plans/2026-07-25-review-doctor-mvp.md`
```

- [ ] **Step 2: 전체 검증**

Run:
1. `cd backend && .venv/bin/python -m pytest -v` → 전체 PASS
2. README의 실행 절차를 처음부터 수행 → 화면 3개 모두 데이터 표시 확인

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README — 실행 방법과 설계 문서 안내"
```
