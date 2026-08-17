# 결제/구독(토스페이먼츠 테스트 연동) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Basic/Pro 구독 플랜을 실제로 차등 적용하고(답글 생성 한도, 광고 순위 모니터링 잠금), 토스페이먼츠 테스트 키로 Pro 업그레이드 결제를 처리하는 기능을 붙인다.

**Architecture:** 백엔드에 `payments` 테이블 1개와 `billing` 라우터(체크아웃/승인/조회)를 신규 추가하고, 결제 승인 시 서버가 금액을 자체 검증한 뒤 토스 confirm API를 호출해 `subscriptions`를 갱신한다. 프론트는 `/account/billing` 페이지에서 토스 결제위젯을 인라인으로 띄우고, `StoreContext`에 `billing` 상태를 추가해 리뷰 관리(답글 한도)와 광고 순위 모니터링(Pro 잠금) 화면이 공유한다.

**Tech Stack:** FastAPI, SQLAlchemy, httpx(이미 backend/requirements.txt에 있음), Next.js 16 / React 19, 토스페이먼츠 결제위젯 JS SDK(신규 설치 필요).

## Global Constraints

- 토스페이먼츠 **테스트 키**(`test_ck_.../test_sk_...`)로만 연동한다. 정기결제(빌링키)는 하지 않는다 — 일회성 결제.
- Pro 월 요금은 **19,900원**(`PRO_MONTHLY_PRICE = 19900`) 상수, 결제 금액은 항상 서버가 결정한다 — 클라이언트가 보낸 금액을 신뢰하지 않는다.
- 만료 판정/일일 한도 리셋은 **KST(Asia/Seoul) 자정** 기준이다. 서버가 UTC로 돌아가도(Railway) 날짜 경계가 어긋나면 안 된다 — `date.today()`를 직접 쓰지 않고 `kst_today()` 헬퍼를 통해서만 오늘 날짜를 구한다.
- `payments.requested_at`/`approved_at`은 schema.sql에서 TIMESTAMPTZ, 코드에서 값을 만들 때는 항상 `datetime.now(timezone.utc)`로 tz-aware 생성한다(naive datetime을 TIMESTAMPTZ 컬럼에 넣어 9시간 밀리는 버그가 이 세션에서 두 번 있었다 — 세 번째 반복 금지).
- 만료 처리는 크론 없이 조회 시점 lazy 판정(`effective_plan`)만 쓴다.
- 백엔드 라우터는 `reviews.py`와 동일하게 `APIRouter(tags=["billing"])`(prefix 없음), 각 라우트 데코레이터에 `/billing/...` 풀 경로를 직접 쓴다.
- 프론트에는 토스트 라이브러리가 없다(조사 확인 완료) — 새로 설치하지 말고, 이 저장소 기존 컨벤션(로컬 `useState` 에러 상태 + `text-danger`/`bg-danger-soft` 인라인 배너)을 그대로 따른다.

---

### Task 1: `payments` 테이블 + `Payment` 모델

**Files:**
- Modify: `schema.sql` (DROP TABLE 목록 21-26행 근처, 테이블 정의는 파일 끝 `-- 21. brand_ad_click_metrics` 블록 다음, `COMMIT;` 이전)
- Modify: `backend/app/models.py` (파일 끝, `class Subscription` 뒤 아무 곳)

**Interfaces:**
- Produces: `Payment` 모델 — `id, user_id, order_id, plan, amount, status, toss_payment_key, fail_reason, requested_at, approved_at`. 이후 모든 태스크가 이 모델을 씀.

- [ ] **Step 1: schema.sql에 payments 테이블 추가**

`schema.sql`의 `DROP TABLE IF EXISTS` 목록(21-26행) 맨 앞에 `payments,`를 추가한다(자식→부모 순서 유지, `payments`가 `users`를 참조하므로 `users`보다 앞):

```sql
DROP TABLE IF EXISTS payments, brand_ad_click_metrics, baemin_shop_brands, review_sync_jobs,
    ...(기존 목록 그대로)...
```

파일 헤더(4행)의 "21개 테이블"을 "22개 테이블"로 바꾼다.

파일 끝 `-- 21. brand_ad_click_metrics` 테이블 블록 다음, `COMMIT;` 직전에 추가:

```sql
-- ----------------------------------------------------------------------------
-- 22. payments — 토스페이먼츠 결제(테스트 키). 일회성 결제만, 정기결제 없음.
-- ----------------------------------------------------------------------------
CREATE TABLE payments (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id         VARCHAR(64) NOT NULL UNIQUE,
    plan             VARCHAR(10) NOT NULL DEFAULT 'pro',
    amount           INT         NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'failed')),
    toss_payment_key VARCHAR(200),
    fail_reason      VARCHAR(200),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at      TIMESTAMPTZ
);

CREATE INDEX idx_payments_user ON payments(user_id);
```

- [ ] **Step 2: models.py에 Payment 모델 추가**

`backend/app/models.py`의 `class Subscription` 블록(95-105행) 뒤에 추가:

```python
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    order_id: Mapped[str] = mapped_column(String(64), unique=True)
    plan: Mapped[str] = mapped_column(String(10), default="pro")
    amount: Mapped[int]
    status: Mapped[str] = mapped_column(String(10), default="pending")
    toss_payment_key: Mapped[str | None] = mapped_column(String(200))
    fail_reason: Mapped[str | None] = mapped_column(String(200))
    requested_at: Mapped[datetime]
    approved_at: Mapped[datetime | None]
```

(`datetime`, `BigInteger`, `Integer`, `String`, `ForeignKey`, `Mapped`, `mapped_column`은 이미 파일 상단에서 import돼 있다 — 다른 모델들과 동일.)

- [ ] **Step 3: 로컬 DB에 반영**

```bash
cd /Users/kunhee/Developer/review-docter
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin <<'EOF'
CREATE TABLE IF NOT EXISTS payments (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id         VARCHAR(64) NOT NULL UNIQUE,
    plan             VARCHAR(10) NOT NULL DEFAULT 'pro',
    amount           INT         NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'failed')),
    toss_payment_key VARCHAR(200),
    fail_reason      VARCHAR(200),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
EOF
```

로컬 backend가 SQLite가 아니라 실제 Postgres(`localhost:15432` 또는 `docker compose`의 `db` 서비스)를 쓰고 있다면 그 DB의 접속 정보에 맞게 명령을 조정한다 — `backend/.env`의 `DATABASE_URL`을 먼저 확인할 것.

- [ ] **Step 4: 회귀 확인**

```bash
cd backend && .venv/bin/pytest -q
```
Expected: 기존 273개 전부 PASS(새 테이블 추가만으로는 아무 것도 안 깨져야 함).

- [ ] **Step 5: 커밋**

```bash
git add schema.sql backend/app/models.py
git commit -m "feat: payments 테이블 추가 (토스페이먼츠 결제 기록)"
```

---

### Task 2: `backend/app/plan.py` — 플랜 판정/날짜 계산 순수 로직

**Files:**
- Create: `backend/app/plan.py`
- Test: `backend/tests/test_plan.py`

**Interfaces:**
- Consumes: `Task 1`의 `Payment`는 쓰지 않음 — `Subscription`, `ReviewReply`, `Review`, `Store`, `User`(기존 `backend/app/models.py`).
- Produces: `PRO_MONTHLY_PRICE: int`, `KST: ZoneInfo`, `kst_today() -> date`, `add_one_month(d: date) -> date`, `effective_plan(sub: Subscription | None) -> str`, `replies_used_today(user: User, db: Session) -> int`. Task 4/5가 전부 이 함수들을 그대로 import해서 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_plan.py`:

```python
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Review, ReviewReply, Store, Subscription
from app.plan import add_one_month, effective_plan, kst_today, replies_used_today


def test_add_one_month_normal():
    assert add_one_month(date(2026, 3, 15)) == date(2026, 4, 15)


def test_add_one_month_clamps_month_end():
    # 1월 31일 + 1개월은 2월 31일이 없으므로 2월 28일(2026은 평년)로 클램핑
    assert add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)


def test_add_one_month_year_rollover():
    assert add_one_month(date(2026, 12, 10)) == date(2027, 1, 10)


def test_effective_plan_none_subscription_is_basic():
    assert effective_plan(None) == "basic"


def test_effective_plan_pro_not_expired():
    sub = Subscription(plan="pro", expires_at=kst_today())
    assert effective_plan(sub) == "pro"


def test_effective_plan_pro_expired_falls_back_to_basic():
    sub = Subscription(plan="pro", expires_at=date(2020, 1, 1))
    assert effective_plan(sub) == "basic"


def test_effective_plan_pro_without_expiry_is_basic():
    # pro인데 expires_at이 없는 상태는 정상 흐름상 나오지 않지만, 방어적으로 basic 취급
    sub = Subscription(plan="pro", expires_at=None)
    assert effective_plan(sub) == "basic"


def test_replies_used_today_counts_only_this_user_and_today(db_session, seeded_user, platforms, reply_styles):
    other_store = Store(user_id=999999, name="다른가게", category="한식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    other_review = Review(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        menu_summary="테스트", rating=5, content="좋아요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([review, other_review])
    db_session.flush()

    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday_kst = now_kst.replace(hour=12) - timedelta(days=1)

    db_session.add_all([
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글1", created_at=now_kst),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="오늘 답글2", created_at=now_kst),
        ReviewReply(review_id=review.id, reply_type="ai_draft", content="어제 답글", created_at=yesterday_kst),
        ReviewReply(review_id=other_review.id, reply_type="ai_draft", content="남의 답글", created_at=now_kst),
    ])
    db_session.commit()

    assert replies_used_today(seeded_user["user"], db_session) == 2
```

주의: `other_store`가 `user_id=999999`처럼 존재하지 않는 유저를 참조하면 FK 제약(SQLite는 기본적으로 FK 체크 비활성)에서 테스트 DB가 SQLite라 통과하지만, 실제 목적은 "다른 유저 소유 매장의 답글은 카운트에서 제외"를 검증하는 것뿐이므로 `user_id`는 임의의 값으로 충분하다.

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_plan.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.plan'`

- [ ] **Step 3: `backend/app/plan.py` 구현**

```python
"""구독 플랜 판정과 날짜 계산 — 순수 로직 + 답글 카운트 조회.

만료/한도 리셋은 전부 KST(Asia/Seoul) 자정 기준이다. 서버가 UTC로 돌아가도
(Railway 배포 환경) 날짜 경계가 어긋나면 안 되므로, 이 모듈 밖에서는
date.today()를 직접 쓰지 말고 kst_today()를 통해서만 "오늘"을 구한다.
"""

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Review, ReviewReply, Store, Subscription, User

KST = ZoneInfo("Asia/Seoul")
PRO_MONTHLY_PRICE = 19900


def kst_today() -> date:
    return datetime.now(KST).date()


def add_one_month(d: date) -> date:
    """달력월 기준 +1개월. 월말은 다음 달 마지막 날로 클램핑한다(1/31 + 1개월 = 2/28)."""
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def effective_plan(sub: Subscription | None) -> str:
    """만료를 조회 시점에 lazy 판정한다(별도 배치/크론 없음)."""
    if sub is None or sub.plan != "pro":
        return "basic"
    if sub.expires_at is not None and sub.expires_at >= kst_today():
        return "pro"
    return "basic"


def _kst_today_range() -> tuple[datetime, datetime]:
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def replies_used_today(user: User, db: Session) -> int:
    start, end = _kst_today_range()
    count = db.scalar(
        select(func.count(ReviewReply.id))
        .join(Review, ReviewReply.review_id == Review.id)
        .join(Store, Review.store_id == Store.id)
        .where(Store.user_id == user.id, ReviewReply.created_at >= start, ReviewReply.created_at < end)
    )
    return count or 0
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_plan.py -v
```
Expected: 8개 전부 PASS. (`test_replies_used_today...`가 `tests.helpers.make_review`를 못 찾으면, 위 테스트 코드에 이미 인라인으로 `Review`를 직접 만들고 있으므로 그 import 줄을 지워도 된다 — 실수로 남겨둔 줄이면 삭제.)

- [ ] **Step 5: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/plan.py backend/tests/test_plan.py
git commit -m "feat: 구독 플랜 판정/달력월 계산/일일 답글 카운트 순수 로직 추가"
```

---

### Task 3: `backend/app/toss_client.py` — 토스 승인 API 래퍼

**Files:**
- Create: `backend/app/toss_client.py`
- Test: `backend/tests/test_toss_client.py`
- Modify: `backend/.env.example` (TOSS_SECRET_KEY 추가)

**Interfaces:**
- Produces: `TossConfirmError(Exception)`, `confirm_payment(payment_key: str, order_id: str, amount: int) -> dict`. Task 4가 이걸 import해서 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_toss_client.py`:

```python
import httpx
import pytest

from app import toss_client


def test_confirm_payment_missing_secret_key_raises(monkeypatch):
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    with pytest.raises(toss_client.TossConfirmError, match="TOSS_SECRET_KEY"):
        toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)


def test_confirm_payment_success(monkeypatch):
    monkeypatch.setenv("TOSS_SECRET_KEY", "test_sk_dummy")

    def fake_post(url, json, headers, timeout):
        assert url == "https://api.tosspayments.com/v1/payments/confirm"
        assert json == {"paymentKey": "pk", "orderId": "oid", "amount": 19900}
        assert headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"status": "DONE", "orderId": "oid"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(toss_client.httpx, "post", fake_post)
    result = toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)
    assert result["status"] == "DONE"


def test_confirm_payment_toss_rejects(monkeypatch):
    monkeypatch.setenv("TOSS_SECRET_KEY", "test_sk_dummy")

    def fake_post(url, json, headers, timeout):
        return httpx.Response(
            400, json={"code": "REJECT_CARD_COMPANY", "message": "카드사에서 결제를 거절했습니다"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(toss_client.httpx, "post", fake_post)
    with pytest.raises(toss_client.TossConfirmError, match="카드사에서 결제를 거절했습니다"):
        toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_toss_client.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.toss_client'`

- [ ] **Step 3: `backend/app/toss_client.py` 구현**

```python
"""토스페이먼츠 결제 승인(confirm) API 래퍼. 테스트 키(test_sk_...)로만 쓴다."""

import base64
import os

import httpx

_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


class TossConfirmError(Exception):
    pass


def confirm_payment(payment_key: str, order_id: str, amount: int) -> dict:
    secret_key = os.environ.get("TOSS_SECRET_KEY", "")
    if not secret_key:
        raise TossConfirmError("TOSS_SECRET_KEY가 설정되지 않았습니다")

    auth = base64.b64encode(f"{secret_key}:".encode()).decode()
    try:
        res = httpx.post(
            _CONFIRM_URL,
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            headers={"Authorization": f"Basic {auth}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise TossConfirmError(f"토스 API 호출 실패: {e}") from e

    if res.status_code != 200:
        try:
            body = res.json()
        except ValueError:
            body = {}
        raise TossConfirmError(body.get("message", f"토스 승인 실패 (status={res.status_code})"))

    return res.json()
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_toss_client.py -v
```
Expected: 3개 전부 PASS.

- [ ] **Step 5: `backend/.env.example`에 키 추가**

`backend/.env.example` 끝에 추가:

```
# 토스페이먼츠 결제 승인 API용 시크릿키(테스트 키만 사용, test_sk_로 시작).
# developers.tosspayments.com 가입 → 상점(테스트) 생성 → API 키 탭에서 발급.
TOSS_SECRET_KEY=
```

- [ ] **Step 6: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/toss_client.py backend/tests/test_toss_client.py backend/.env.example
git commit -m "feat: 토스페이먼츠 결제 승인 API 래퍼 추가"
```

---

### Task 4: `backend/app/routers/billing.py` — 체크아웃/승인/조회 API

**Files:**
- Create: `backend/app/routers/billing.py`
- Test: `backend/tests/test_billing.py`
- Modify: `backend/app/main.py:6` (import), 끝 부분(`include_router`)

**Interfaces:**
- Consumes: Task 1의 `Payment`, Task 2의 `PRO_MONTHLY_PRICE`/`add_one_month`/`effective_plan`/`kst_today`/`replies_used_today`, Task 3의 `confirm_payment`/`TossConfirmError`. `backend/app/auth.py`의 `get_current_user`(`from app.auth import get_current_user`).
- Produces: `GET /billing/me`, `POST /billing/checkout`, `POST /billing/confirm`, `GET /billing/history`. Task 5(답글 한도)와 Task 6(프론트 StoreContext)가 `GET /billing/me`의 응답 형태(`plan, is_pro, expires_at, daily_reply_limit, replies_used_today`)를 그대로 소비한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_billing.py`:

```python
from datetime import date, datetime, timedelta, timezone

from app.models import Payment, Subscription
from app.plan import add_one_month, kst_today
from app.toss_client import TossConfirmError


def test_billing_me_basic_default(client, seeded_user, auth_headers):
    res = client.get("/billing/me", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "basic"
    assert body["is_pro"] is False
    assert body["daily_reply_limit"] == 10
    assert body["replies_used_today"] == 0


def test_checkout_ignores_client_amount_and_uses_server_price(client, db_session, seeded_user, auth_headers):
    res = client.post("/billing/checkout", json={}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["amount"] == 19900
    payment = db_session.query(Payment).filter_by(order_id=body["order_id"]).one()
    assert payment.status == "pending"
    assert payment.amount == 19900
    assert payment.user_id == seeded_user["user"].id


def test_confirm_success_upgrades_subscription(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(payment_key, order_id, amount):
        assert order_id == checkout["order_id"]
        assert amount == 19900
        return {"status": "DONE"}

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "test-pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["plan"] == "pro"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "pro"
    assert sub.expires_at == add_one_month(kst_today())

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "approved"
    assert payment.toss_payment_key == "test-pk"


def test_confirm_extends_existing_pro_period_instead_of_resetting(client, db_session, seeded_user, auth_headers, monkeypatch):
    future_expiry = kst_today() + timedelta(days=10)
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": future_expiry}
    )
    db_session.commit()

    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE"})

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk2", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.expires_at == add_one_month(future_expiry)  # 오늘이 아니라 기존 만료일부터 +1개월


def test_confirm_rejects_amount_mismatch_without_calling_toss(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    called = []
    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: called.append(kw) or {"status": "DONE"})

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 1},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert called == []  # 토스 API 자체를 호출하지 않아야 함

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 건드리지 않음


def test_confirm_rejects_other_users_order_id(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from app.models import Store, Subscription as SubscriptionModel, User

    other_user = User(
        email="other@dris.kr", password_hash="x", nickname="다른사장",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()
    db_session.add(Payment(
        user_id=other_user.id, order_id="other-order", plan="pro", amount=19900,
        status="pending", requested_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr("app.routers.billing.confirm_payment", lambda **kw: {"status": "DONE"})
    res = client.post(
        "/billing/confirm",
        json={"order_id": "other-order", "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 404


def test_confirm_toss_failure_marks_payment_failed_without_upgrading(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(**kw):
        raise TossConfirmError("카드사에서 결제를 거절했습니다")

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"
    assert "카드사" in payment.fail_reason

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"


def test_billing_history_returns_users_payments_newest_first(client, db_session, seeded_user, auth_headers):
    db_session.add_all([
        Payment(user_id=seeded_user["user"].id, order_id="old", plan="pro", amount=19900, status="approved",
                requested_at=datetime.now(timezone.utc) - timedelta(days=30)),
        Payment(user_id=seeded_user["user"].id, order_id="new", plan="pro", amount=19900, status="approved",
                requested_at=datetime.now(timezone.utc)),
    ])
    db_session.commit()

    res = client.get("/billing/history", headers=auth_headers)
    assert res.status_code == 200
    order_ids = [p["order_id"] for p in res.json()]
    assert order_ids == ["new", "old"]
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -v
```
Expected: FAIL — `404 Not Found`(라우터가 아직 없음).

- [ ] **Step 3: `backend/app/routers/billing.py` 구현**

```python
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Payment, Subscription, User
from app.plan import PRO_MONTHLY_PRICE, add_one_month, effective_plan, kst_today, replies_used_today
from app.toss_client import TossConfirmError, confirm_payment

router = APIRouter(tags=["billing"])


class MeBillingResponse(BaseModel):
    plan: str
    is_pro: bool
    expires_at: date | None
    daily_reply_limit: int
    replies_used_today: int


@router.get("/billing/me", response_model=MeBillingResponse)
def billing_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    plan = effective_plan(sub)
    return MeBillingResponse(
        plan=plan,
        is_pro=plan == "pro",
        expires_at=sub.expires_at if sub else None,
        daily_reply_limit=sub.daily_reply_limit if sub else 10,
        replies_used_today=replies_used_today(user, db),
    )


class CheckoutResponse(BaseModel):
    order_id: str
    amount: int
    order_name: str


@router.post("/billing/checkout", response_model=CheckoutResponse)
def checkout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order_id = f"pro-{uuid.uuid4().hex}"
    payment = Payment(
        user_id=user.id, order_id=order_id, plan="pro", amount=PRO_MONTHLY_PRICE,
        status="pending", requested_at=datetime.now(timezone.utc),
    )
    db.add(payment)
    db.commit()
    return CheckoutResponse(order_id=order_id, amount=PRO_MONTHLY_PRICE, order_name="Pro 플랜 1개월")


class ConfirmRequest(BaseModel):
    order_id: str
    payment_key: str
    amount: int


class ConfirmResponse(BaseModel):
    status: str
    plan: str
    expires_at: date


@router.post("/billing/confirm", response_model=ConfirmResponse)
def confirm(body: ConfirmRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = db.scalar(select(Payment).where(Payment.order_id == body.order_id))
    if payment is None or payment.user_id != user.id:
        raise HTTPException(404, "결제 요청을 찾을 수 없습니다")
    if payment.status != "pending":
        raise HTTPException(400, "이미 처리된 결제입니다")
    if payment.amount != body.amount:
        raise HTTPException(400, "결제 금액이 일치하지 않습니다")

    try:
        confirm_payment(payment_key=body.payment_key, order_id=payment.order_id, amount=payment.amount)
    except TossConfirmError as e:
        payment.status = "failed"
        payment.fail_reason = str(e)[:200]
        db.commit()
        raise HTTPException(402, f"결제 승인 실패: {e}")

    payment.status = "approved"
    payment.toss_payment_key = body.payment_key
    payment.approved_at = datetime.now(timezone.utc)

    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if sub is None:
        sub = Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=kst_today())
        db.add(sub)
        db.flush()

    today = kst_today()
    base = sub.expires_at if (sub.expires_at is not None and sub.expires_at > today) else today
    sub.plan = "pro"
    sub.expires_at = add_one_month(base)

    db.commit()
    return ConfirmResponse(status="approved", plan=sub.plan, expires_at=sub.expires_at)


class PaymentHistoryItem(BaseModel):
    order_id: str
    amount: int
    status: str
    requested_at: datetime
    approved_at: datetime | None

    class Config:
        from_attributes = True


@router.get("/billing/history", response_model=list[PaymentHistoryItem])
def billing_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payments = db.scalars(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.requested_at.desc())
    ).all()
    return list(payments)
```

- [ ] **Step 4: main.py에 라우터 등록**

`backend/app/main.py:6`:
```python
from app.routers import ads, auth, billing, dashboard, orders, reply_settings, reviews, sales, store_connections
```

`include_router` 목록 끝에 추가:
```python
app.include_router(billing.router)
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -v
```
Expected: 8개 전부 PASS.

- [ ] **Step 6: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/routers/billing.py backend/app/main.py backend/tests/test_billing.py
git commit -m "feat: /billing/me,checkout,confirm,history API 추가 (토스페이먼츠 테스트 연동)"
```

---

### Task 5: 답글 생성 일일 한도 강제

**Files:**
- Modify: `backend/app/routers/reviews.py:103-131`(`generate_reply` 및 그 앞의 `GenerateReplyRequest`)
- Modify: `backend/tests/test_reviews.py`

**Interfaces:**
- Consumes: Task 2의 `effective_plan`, `replies_used_today`.
- Produces: `POST /reviews/{review_id}/generate-reply`가 Basic 플랜 유저의 오늘(KST) 생성분이 `daily_reply_limit` 이상이면 403 `{"detail": {"message": "...", "error_code": "reply_limit_exceeded"}}`을 반환. Task 8(프론트 리뷰 관리 화면)이 이 `error_code` 문자열을 그대로 분기 조건으로 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_reviews.py` 끝에 추가(기존 import에 `Subscription`/`date`가 없으면 파일 상단 import에 추가, 이미 `datetime`을 다른 방식으로 import했다면 이름 충돌 없이 병합):

```python
from datetime import date

from app.models import Subscription


def test_generate_reply_blocks_after_daily_limit_for_basic_plan(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    reviews = [make_review(db_session, seeded_user["store"], platforms, rating=5) for _ in range(11)]

    for review in reviews[:10]:
        res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
        assert res.status_code == 200

    res = client.post(f"/reviews/{reviews[10].id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "reply_limit_exceeded"


def test_generate_reply_unlimited_for_pro_plan(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()

    reviews = [make_review(db_session, seeded_user["store"], platforms, rating=5) for _ in range(11)]
    for review in reviews:
        res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
        assert res.status_code == 200
```

`make_review` 헬퍼가 이미 `test_reviews.py` 상단에 정의돼 있는지 확인 후, 없으면 기존 다른 테스트가 리뷰를 만드는 방식을 그대로 함수로 추출해서 재사용한다(이미 17-29행 근처 테스트가 `make_review(db_session, seeded_user["store"], platforms, rating=5)`를 쓰고 있으므로 존재할 가능성이 높다).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_reviews.py -k daily_limit -v
```
Expected: FAIL — 11번째 요청도 200이 나옴(아직 한도 체크가 없으므로).

- [ ] **Step 3: `generate_reply`에 한도 체크 삽입**

`backend/app/routers/reviews.py` 상단 import에 추가:
```python
from app.plan import effective_plan, replies_used_today
```
(이미 `from app.models import ...`에 `Subscription`이 없으면 추가.)

118행(`if style is None: raise HTTPException(404, "답글 스타일 없음")`) 다음, 120행(`template = {...}`) 이전에 삽입:

```python
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if effective_plan(sub) == "basic":
        limit = sub.daily_reply_limit if sub else 10
        if replies_used_today(user, db) >= limit:
            raise HTTPException(
                403,
                detail={"message": "오늘 답글 생성 한도를 모두 사용했어요. Pro는 무제한이에요.", "error_code": "reply_limit_exceeded"},
            )
```

(`select`가 이미 `reviews.py` 상단에서 import돼 있는지 확인 — 안 돼있으면 `from sqlalchemy import select` 추가.)

- [ ] **Step 4: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_reviews.py -v
```
Expected: 전부 PASS(기존 테스트 포함 회귀 없음).

- [ ] **Step 5: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/routers/reviews.py backend/tests/test_reviews.py
git commit -m "feat: Basic 플랜 답글 생성 일일 한도(10건) 백엔드 강제"
```

---

### Task 6: 프론트 공통 배선 — `ApiError.errorCode`, `StoreContext.billing`, 사이드바

**Files:**
- Modify: `frontend/src/lib/api.ts:17-23`(`ApiError`), `:44-47`(에러 파싱)
- Modify: `frontend/src/lib/store-context.tsx`(전체)
- Modify: `frontend/src/components/Sidebar.tsx:7-58`(ICONS), `:82-87`(NAV)

**Interfaces:**
- Produces: `ApiError.errorCode?: string`. `useStoreContext()`가 `billing: BillingResponse | null`, `refreshBilling: () => Promise<void>`를 반환. Task 7/8이 전부 이걸 씀.

- [ ] **Step 1: `api.ts` — 에러 응답에서 `error_code` 추출**

`frontend/src/lib/api.ts:17-23`을 교체:

```ts
export class ApiError extends Error {
  status: number;
  errorCode?: string;
  constructor(status: number, message: string, errorCode?: string) {
    super(message);
    this.status = status;
    this.errorCode = errorCode;
  }
}
```

`:44-47`(`if (!res.ok) { ... }`)을 교체:

```ts
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : (detail?.message ?? `요청 실패 (${res.status})`);
    const errorCode = typeof detail === "object" && detail !== null ? detail.error_code : undefined;
    throw new ApiError(res.status, message, errorCode);
  }
```

(백엔드 `HTTPException(403, detail={"message": ..., "error_code": ...})`는 FastAPI가 `{"detail": {"message": ..., "error_code": ...}}`로 감싸서 응답한다 — `detail`이 문자열이 아니라 객체인 케이스를 여기서 분기하는 것.)

- [ ] **Step 2: `store-context.tsx`에 billing 추가**

전체 파일을 아래로 교체(기존 `user`/`stores`/`storeId`/`ready`/`logout`/`refreshUser` 로직은 그대로 유지, `billing`/`refreshBilling`만 추가):

```tsx
"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, clearToken, getToken } from "@/lib/api";

type MeResponse = { id: number; email: string | null; nickname: string; has_phone: boolean; marketing_agreed: boolean };
type StoreOption = { id: number; name: string; category: string };
type BillingResponse = {
  plan: string;
  is_pro: boolean;
  expires_at: string | null;
  daily_reply_limit: number;
  replies_used_today: number;
};

type StoreContextValue = {
  user: MeResponse | null;
  stores: StoreOption[];
  storeId: number | null;
  setStoreId: (id: number) => void;
  billing: BillingResponse | null;
  refreshBilling: () => Promise<void>;
  ready: boolean;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const StoreContext = createContext<StoreContextValue | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<MeResponse | null>(null);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [storeId, setStoreIdState] = useState<number | null>(null);
  const [billing, setBilling] = useState<BillingResponse | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    Promise.all([
      apiGet<MeResponse>("/auth/me"),
      apiGet<StoreOption[]>("/stores"),
      apiGet<BillingResponse>("/billing/me"),
    ])
      .then(([me, storeList, billingInfo]) => {
        setUser(me);
        setStores(storeList);
        setBilling(billingInfo);
        const saved = Number(window.localStorage.getItem("dris_store_id"));
        const initial = storeList.find((s) => s.id === saved)?.id ?? storeList[0]?.id ?? null;
        setStoreIdState(initial);
        setReady(true);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  const setStoreId = useCallback((id: number) => {
    setStoreIdState(id);
    window.localStorage.setItem("dris_store_id", String(id));
  }, []);

  const logout = useCallback(() => {
    clearToken();
    router.replace("/login");
  }, [router]);

  const refreshUser = useCallback(async () => {
    setUser(await apiGet<MeResponse>("/auth/me"));
  }, []);

  const refreshBilling = useCallback(async () => {
    setBilling(await apiGet<BillingResponse>("/billing/me"));
  }, []);

  return (
    <StoreContext.Provider
      value={{ user, stores, storeId, setStoreId, billing, refreshBilling, ready, logout, refreshUser }}
    >
      {children}
    </StoreContext.Provider>
  );
}

export function useStoreContext() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStoreContext must be used within StoreProvider");
  return ctx;
}
```

- [ ] **Step 3: 사이드바에 "구독 관리" 추가**

`frontend/src/components/Sidebar.tsx`의 `ICONS` 객체(7-58행)에 추가:

```tsx
  billing: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <rect x="2.5" y="5" width="15" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M2.5 8.5h15" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  ),
```

`NAV`의 "내 정보 관리" 섹션(82-87행)을 교체:

```tsx
  {
    header: "내 정보 관리",
    items: [
      { href: "/account/stores", label: "가게 연결", icon: "store" },
      { href: "/account/profile", label: "계정 관리", icon: "account" },
      { href: "/account/billing", label: "구독 관리", icon: "billing" },
    ],
  },
```

- [ ] **Step 4: 프론트 타입 체크**

```bash
cd frontend && npx tsc --noEmit
```
Expected: 에러 없음(`/billing/me`를 아직 백엔드가 서빙하지만 프론트 개발 서버는 안 켜져 있어도 타입 체크는 통과해야 함 — Task 4에서 백엔드는 이미 완료됨).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/store-context.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: 프론트에 billing 상태(StoreContext)와 구독 관리 사이드바 항목 추가"
```

---

### Task 7: `/account/billing` 페이지 — 토스 결제위젯 연동

**Files:**
- Create: `frontend/src/app/(app)/account/billing/page.tsx`
- Modify: `frontend/package.json`(토스 SDK 의존성 추가)
- Modify: `frontend/.env.example`(`NEXT_PUBLIC_TOSS_CLIENT_KEY` 추가)

**Interfaces:**
- Consumes: Task 6의 `useStoreContext().billing`/`refreshBilling`, `apiGet`/`apiPost`(`frontend/src/lib/api.ts`).
- Produces: `/account/billing` 경로. Task 8의 success/fail 페이지가 여기로 복귀 링크를 건다.

- [ ] **Step 1: 토스 결제위젯 최신 연동 가이드 실측 확인 (코드 작성 전 필수)**

이 계획을 쓴 시점에 토스페이먼츠 개발자센터 문서 사이트가 SPA라 자동 조회로 정확한 npm 패키지명/초기화 함수 시그니처를 못 가져왔다. 아래 스텝의 코드는 토스 결제위젯 SDK v2 기준 **최선의 추정**이다 — 구현 전에 브라우저로 `https://docs.tosspayments.com/guides/v2/payment-widget/integration`(또는 토스 개발자센터의 "결제위젯 연동하기" 최신 가이드)을 직접 열어서:
- npm 패키지명이 `@tosspayments/tosspayments-sdk`가 맞는지
- 초기화 함수가 `loadTossPayments(clientKey)` 또는 `TossPayments(clientKey)`인지
- `widgets()`, `setAmount()`, `renderPaymentMethods()`, `renderAgreement()`, `requestPayment()` 메서드명과 파라미터가 아래 코드와 일치하는지

를 확인하고, 다르면 아래 Step 2 코드를 실제 가이드에 맞게 고쳐서 진행한다(이 저장소의 "실 계정 라이브 검증" 컨벤션과 동일하게 취급 — 이 확인 없이 그대로 커밋하지 않는다).

- [ ] **Step 2: 패키지 설치**

```bash
cd frontend && npm install @tosspayments/tosspayments-sdk
```
(Step 1에서 다른 패키지명으로 확인됐다면 그 이름으로 설치.)

- [ ] **Step 3: `frontend/.env.example`에 클라이언트키 추가**

```
# 토스페이먼츠 결제위젯 클라이언트키(테스트 키만 사용, test_ck_로 시작). 브라우저에
# 노출되는 값이라 NEXT_PUBLIC_ 접두어 필수. developers.tosspayments.com에서 발급.
NEXT_PUBLIC_TOSS_CLIENT_KEY=
```

- [ ] **Step 4: `/account/billing/page.tsx` 작성**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { loadTossPayments } from "@tosspayments/tosspayments-sdk";
import { Card } from "@/components/Card";
import { apiGet, apiPost, ApiError, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type PaymentHistoryItem = {
  order_id: string;
  amount: number;
  status: "pending" | "approved" | "failed";
  requested_at: string;
  approved_at: string | null;
};

const STATUS_LABEL: Record<PaymentHistoryItem["status"], string> = {
  pending: "대기중",
  approved: "승인완료",
  failed: "실패",
};

export default function BillingPage() {
  const { billing, refreshBilling } = useStoreContext();
  const [history, setHistory] = useState<PaymentHistoryItem[]>([]);
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const widgetContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiGet<PaymentHistoryItem[]>("/billing/history").then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    if (!checkoutOpen) return;
    const clientKey = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY;
    if (!clientKey) {
      setError("토스페이먼츠 클라이언트키가 설정되지 않았습니다. NEXT_PUBLIC_TOSS_CLIENT_KEY를 확인해주세요.");
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        const checkout = await apiPost<{ order_id: string; amount: number; order_name: string }>(
          "/billing/checkout",
          {},
        );
        if (cancelled) return;

        const tossPayments = await loadTossPayments(clientKey);
        const widgets = tossPayments.widgets({ customerKey: `user-${Date.now()}` });
        await widgets.setAmount({ currency: "KRW", value: checkout.amount });
        await widgets.renderPaymentMethods({ selector: "#toss-payment-method" });
        await widgets.renderAgreement({ selector: "#toss-agreement" });

        const submitButton = document.getElementById("toss-submit");
        submitButton?.addEventListener("click", async () => {
          try {
            await widgets.requestPayment({
              orderId: checkout.order_id,
              orderName: checkout.order_name,
              successUrl: `${window.location.origin}/account/billing/success`,
              failUrl: `${window.location.origin}/account/billing/fail`,
            });
          } catch {
            // 토스가 successUrl/failUrl로 리다이렉트하므로 여기서는 별도 처리 없음
          }
        });
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "결제 위젯을 불러오지 못했습니다.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [checkoutOpen]);

  const isPro = billing?.is_pro ?? false;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">구독 관리</h1>
        <p className="text-sm text-muted">플랜과 결제 내역을 확인하고 Pro로 업그레이드할 수 있어요.</p>
      </div>

      <Card title="현재 구독">
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted">현재 요금제</span>
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium ${isPro ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted"}`}
          >
            {isPro ? "Pro" : "Basic"}
          </span>
        </div>
        {isPro && billing?.expires_at && (
          <p className="mt-2 text-sm text-muted">다음 결제 예정일: {billing.expires_at}</p>
        )}

        <div className="mt-4">
          <p className="mb-2 text-sm text-muted">결제 내역</p>
          {history.length === 0 ? (
            <p className="text-sm text-muted">결제 내역이 없습니다.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs text-muted">
                  <th className="py-2 text-left">일시</th>
                  <th className="py-2 text-left">금액</th>
                  <th className="py-2 text-left">상태</th>
                </tr>
              </thead>
              <tbody>
                {history.map((p) => (
                  <tr key={p.order_id} className="border-b border-border-subtle last:border-0">
                    <td className="py-2">{new Date(p.requested_at).toLocaleString("ko-KR")}</td>
                    <td className="py-2">{won(p.amount)}</td>
                    <td className="py-2">{STATUS_LABEL[p.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card title="Basic">
          <p className="text-2xl font-semibold">무료</p>
          <ul className="mt-3 space-y-1.5 text-sm text-muted">
            <li>답글 생성 하루 10건</li>
            <li>광고 순위 모니터링 🔒</li>
            <li>리뷰 관리·매출·정산·주문내역·재주문율 통계</li>
          </ul>
          {!isPro && (
            <div className="mt-4 rounded-lg border border-border-subtle py-2.5 text-center text-sm text-muted">
              현재 플랜
            </div>
          )}
        </Card>

        <Card title="Pro">
          <p className="text-2xl font-semibold">
            {won(19900)}
            <span className="text-sm font-normal text-muted"> /월</span>
          </p>
          <ul className="mt-3 space-y-1.5 text-sm text-muted">
            <li>답글 생성 무제한</li>
            <li>광고 순위 모니터링 전체 이용</li>
            <li>Basic의 모든 기능 포함</li>
          </ul>
          {isPro ? (
            <div className="mt-4 rounded-lg bg-accent-soft py-2.5 text-center text-sm text-accent">현재 플랜</div>
          ) : (
            <button
              onClick={() => setCheckoutOpen(true)}
              className="mt-4 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90"
            >
              Pro 시작하기
            </button>
          )}
        </Card>
      </div>

      {checkoutOpen && !isPro && (
        <Card title="결제하기">
          {error && <p className="mb-3 text-sm text-danger">{error}</p>}
          <div id="toss-payment-method" />
          <div id="toss-agreement" className="mt-3" />
          <button
            id="toss-submit"
            className="mt-4 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90"
          >
            {won(19900)} 결제하기
          </button>
        </Card>
      )}
    </div>
  );
}
```

`refreshBilling`은 이 페이지가 아니라 Task 8의 success 페이지에서 호출한다(결제 승인은 그 화면에서 일어나므로).

- [ ] **Step 5: 브라우저 라이브 확인**

```bash
cd frontend && npm run dev
```
`http://localhost:3000/account/billing`을 열어 Basic/Pro 카드가 렌더링되는지, "Pro 시작하기" 클릭 시 결제 위젯 섹션이 펼쳐지는지 확인한다. `NEXT_PUBLIC_TOSS_CLIENT_KEY`가 비어있으면 에러 문구가 뜨는 게 정상(사용자가 아직 키를 발급받기 전이므로) — 이 상태에서도 페이지가 깨지지 않고 에러 배너만 뜨는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/app/'(app)'/account/billing/page.tsx frontend/package.json frontend/package-lock.json frontend/.env.example
git commit -m "feat: 구독 관리 화면에 토스페이먼츠 결제위젯 인라인 연동"
```

---

### Task 8: 결제 결과 페이지 + 답글 한도 배지 + 광고 순위 Pro 잠금

**Files:**
- Create: `frontend/src/app/(app)/account/billing/success/page.tsx`
- Create: `frontend/src/app/(app)/account/billing/fail/page.tsx`
- Modify: `frontend/src/app/(app)/reviews/page.tsx`(상단 배지, `generate` 함수 에러 처리)
- Modify: `frontend/src/app/(app)/ads/page.tsx`(Pro 잠금)

**Interfaces:**
- Consumes: Task 6의 `useStoreContext().billing`/`refreshBilling`, Task 4의 `POST /billing/confirm` 응답 형태, Task 5의 `error_code: "reply_limit_exceeded"`.

- [ ] **Step 1: `/account/billing/success/page.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

export default function BillingSuccessPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refreshBilling } = useStoreContext();
  const [state, setState] = useState<"loading" | "done" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const orderId = params.get("orderId");
    const paymentKey = params.get("paymentKey");
    const amount = params.get("amount");
    if (!orderId || !paymentKey || !amount) {
      setState("error");
      setMessage("결제 정보가 올바르지 않습니다.");
      return;
    }

    apiPost("/billing/confirm", { order_id: orderId, payment_key: paymentKey, amount: Number(amount) })
      .then(async () => {
        await refreshBilling();
        setState("done");
      })
      .catch((e) => {
        setState("error");
        setMessage(e instanceof ApiError ? e.message : "결제 승인 중 오류가 발생했습니다.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      {state === "loading" && <p className="text-sm text-muted">결제를 확인하고 있어요...</p>}
      {state === "done" && (
        <>
          <p className="text-lg font-semibold">Pro 플랜이 시작됐어요!</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
      {state === "error" && (
        <>
          <p className="text-sm text-danger">{message}</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg border border-border-subtle px-4 py-2 text-sm"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `/account/billing/fail/page.tsx`**

```tsx
"use client";

import { useRouter, useSearchParams } from "next/navigation";

export default function BillingFailPage() {
  const params = useSearchParams();
  const router = useRouter();
  const message = params.get("message") ?? "결제가 취소되었거나 실패했습니다.";

  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      <p className="text-sm text-danger">{message}</p>
      <button
        onClick={() => router.push("/account/billing")}
        className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
      >
        다시 시도하기
      </button>
    </div>
  );
}
```

- [ ] **Step 3: 리뷰 관리 화면에 답글 한도 배지 + 403 처리**

`frontend/src/app/(app)/reviews/page.tsx` 상단(리스트/필터 UI 시작 지점, 조사에서 확인된 헤더 영역)에 배지 추가:

```tsx
const { billing } = useStoreContext();
```

헤더 근처(예: 페이지 제목 `<h1>` 바로 아래)에:

```tsx
{billing && (
  <p className="text-xs text-muted">
    오늘 답글 생성{" "}
    {billing.is_pro ? "무제한 (Pro)" : `${billing.replies_used_today}/${billing.daily_reply_limit} (Basic)`}
  </p>
)}
```

기존 `generate` 함수(118-130행 근처, `catch` 없이 `try/finally`만 있던 부분)를 `catch`로 감싸서 한도 초과를 인라인 배너로 표시:

```tsx
const [generateError, setGenerateError] = useState<string | null>(null);

async function generate(...) {
  setGenerateError(null);
  try {
    // 기존 본문 그대로
  } catch (e) {
    if (e instanceof ApiError && e.errorCode === "reply_limit_exceeded") {
      setGenerateError(e.message);
    } else {
      setGenerateError(e instanceof ApiError ? e.message : "답글 생성에 실패했습니다.");
    }
  } finally {
    // 기존 finally 그대로
  }
}
```

버튼 근처에 배너 렌더링:

```tsx
{generateError && (
  <p className="mt-2 text-xs text-danger">
    {generateError}{" "}
    <Link href="/account/billing" className="underline">
      구독 관리
    </Link>
  </p>
)}
```

(`ApiError`, `Link`가 이미 파일 상단에 import돼 있는지 확인 — `ApiError`는 `@/lib/api`에서, `Link`는 `next/link`에서.)

- [ ] **Step 4: 광고 순위 모니터링 Pro 잠금**

`frontend/src/app/(app)/ads/page.tsx` 최상단, 데이터 fetch용 `useEffect`(72-77행 근처) 앞에:

```tsx
const { billing } = useStoreContext();
```

컴포넌트의 return 문 전체를 아래처럼 조건부로 감싼다(기존 JSX는 `billing?.is_pro`가 true일 때만 렌더링):

```tsx
if (billing && !billing.is_pro) {
  return (
    <div className="mx-auto max-w-md space-y-4 py-24 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">
        🔒
      </div>
      <p className="text-lg font-semibold">Pro 전용 기능입니다</p>
      <p className="text-sm text-muted">광고 순위 모니터링은 Pro 플랜에서 이용할 수 있어요.</p>
      <Link
        href="/account/billing"
        className="inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
      >
        Pro 시작하기
      </Link>
    </div>
  );
}
```

기존 데이터 fetch `useEffect`도 Basic 유저면 API를 안 부르도록 조건 추가:

```tsx
useEffect(() => {
  if (!storeId || (billing && !billing.is_pro)) return;
  // 기존 fetch 로직 그대로
}, [storeId, billing]);
```

(`Link`가 이미 import돼 있는지 확인 — 없으면 `import Link from "next/link";` 추가.)

- [ ] **Step 5: 브라우저 라이브 확인**

```bash
cd frontend && npm run dev
```
1. `/reviews`에서 답글 생성 버튼 눌러 정상 동작 확인, 배지가 "0/10 (Basic)" → 생성할 때마다 올라가는지 확인(페이지 새로고침 없이는 `billing`이 갱신 안 될 수 있음 — 갱신이 필요하면 `generate` 성공 시 `refreshBilling()` 호출 추가).
2. `/ads`에서 (현재 seed 데이터가 Basic이면) 잠금 화면이 뜨는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/app/'(app)'/account/billing/success frontend/src/app/'(app)'/account/billing/fail frontend/src/app/'(app)'/reviews/page.tsx frontend/src/app/'(app)'/ads/page.tsx
git commit -m "feat: 결제 성공/실패 화면, 답글 한도 배지, 광고 순위 모니터링 Pro 잠금"
```

---

### Task 9: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 없음(문서 전용 태스크, 이전 8개 태스크가 실제로 구현한 내용을 기록).

- [ ] **Step 1: "방향 전환" 로드맵에 완료 표시**

`CLAUDE.md`의 "방향 전환(2026-08-06)" 절, 로드맵 2번 항목(`2. 결제/구독 (PG사 테스트 연동)`) 다음 줄에 추가:
```
   (2026-08-17 완료 — 아래 "결제/구독 연동" 절 참고)
```

- [ ] **Step 2: "결제/구독 연동 (예외 허용, 테스트 모드)" 절 신규 추가**

"절대 금지" 목록(28-37행) 바로 다음, 기존 "카카오 소셜 로그인" 절 앞에 삽입:

```markdown
### 결제/구독 연동 (예외 허용, 테스트 모드)
원래 "실제 결제, 구독... 자동화 금지"였으나, 실 SaaS 전환 로드맵 2번으로
토스페이먼츠 **테스트 키**(`test_ck_.../test_sk_...`) 연동을 실제로 붙이기로
결정했다(2026-08-17). 테스트 키는 실제 카드사망을 타지 않아 구조적으로
진짜 돈이 움직이지 않는다 — 운영 키(`live_ck_.../live_sk_...`) 전환은 완전히
별도 승인이 필요한 범위 밖이고 아직 하지 않았다. 정기결제(빌링키/자동
재결제)도 하지 않는다 — 사용자가 매달 수동으로 다시 결제하는 일회성
결제만 지원한다.

Basic/Pro 플랜 차이를 이번에 처음 실제로 정의했다: 답글 생성 일일 한도
(Basic 10건, Pro 무제한, `backend/app/routers/reviews.py`의
`generate_reply`가 강제)와 광고 순위 모니터링(Basic은 프론트에서 잠금,
`frontend/src/app/(app)/ads/page.tsx`). 결제 승인은 `backend/app/routers/
billing.py`가 처리한다 — 프론트가 `POST /billing/checkout`으로 서버가
결정한 금액(`PRO_MONTHLY_PRICE=19900원`)의 주문을 만들고, 토스 결제위젯
결제 후 `POST /billing/confirm`에서 **금액을 서버 DB에 저장된 값과
대조 검증한 뒤**(클라이언트가 보낸 금액을 신뢰하지 않음) 토스 승인 API를
호출한다. 만료 판정은 크론 없이 조회 시점 lazy 판정(`backend/app/
plan.py`의 `effective_plan`)이고, 모든 날짜 경계는 KST(Asia/Seoul)
자정 기준이다. 설계 상세는
`docs/superpowers/specs/2026-08-17-toss-payments-subscription-design.md`
참고.
```

- [ ] **Step 3: DB 설계/포함 기능 목록 갱신**

"DB 설계" 절의 테이블 목록에 `payments` 추가, "테이블 용도" 목록에:
```
- payments: 토스페이먼츠 결제 기록(테스트 키). 일회성 결제만, 정기결제 없음.
```

"포함 기능" 목록(기존 "가게-플랫폼 연결, 구독 플랜." 부분)을 "가게-플랫폼 연결,
구독 관리(결제 포함)."로 갱신.

- [ ] **Step 4: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: 결제/구독(토스페이먼츠 테스트 연동) CLAUDE.md 반영"
```
