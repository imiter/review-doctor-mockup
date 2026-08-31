# 관리자 페이지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 결제 이력 조회, 배민 연결 매장 운영 현황(동기화 상태+자동답글 스위치), 유저 검색+플랜 수동 변경 세 화면으로 구성된 최소 범위 관리자 페이지를 추가한다.

**Architecture:** `users.role` 컬럼(owner/admin) 기반 인가. 백엔드는 기존 `require_pro_plan`과 동일한 FastAPI 의존성 패턴(`require_admin`)으로 `/admin/*` 5개 엔드포인트를 보호한다. 프론트는 같은 Next.js 앱 안에 예측 불가능한 슬러그 경로(`/ops-4k9x2m`)로 새 라우트 그룹을 추가하고, 기존 다크 테마 토큰·컴포넌트 패턴을 재사용한다. 부수적으로 `/auth/login`에 이메일 기준 브루트포스 잠금을 추가한다.

**Tech Stack:** FastAPI + SQLAlchemy + pytest(SQLite 인메모리) · Next.js(App Router) + TypeScript

## Global Constraints

- 새 DB 마이그레이션 도구 없음 — `schema.sql`이 정본, 프로덕션은 수동 SQL로 반영(Alembic 미사용, 기존 프로젝트 원칙)
- 관리자 로그인은 기존 `/auth/login`을 그대로 씀 — 별도 관리자 로그인 폼 없음
- 관리자 경로는 `/ops-4k9x2m` (예측 불가능한 슬러그, `docs/superpowers/specs/2026-09-01-admin-panel-design.md` 참고)
- 새 디자인 시스템 없음 — `globals.css` 기존 CSS 변수, 기존 pill/카드 패턴만 재사용
- 프론트 단위 테스트 없음(이 프로젝트 기존 관행) — `npx tsc --noEmit`과 브라우저 수동 확인으로 검증
- 결제 환불, 자동화 전체 킬스위치, 관리자 액션 감사 로그, 관리자 초대 플로우는 범위 밖(스펙 문서의 "의도적으로 뺀 것" 참고)

---

## Task 1: `users.role` 컬럼 추가

**Files:**
- Modify: `schema.sql` (users 테이블 정의, 34~42번째 줄 부근)
- Modify: `backend/app/models.py` (`User` 클래스, 18~31번째 줄 부근)
- Test: `backend/tests/test_admin.py` (새 파일)

**Interfaces:**
- Produces: `User.role: str` (기본값 `"owner"`, `"admin"`이면 관리자) — 이후 모든 태스크가 이 필드로 관리자를 판별한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_admin.py` 새로 생성:

```python
from datetime import datetime, timezone

from app.models import User


def test_user_role_defaults_to_owner(db_session):
    user = User(nickname="테스트", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.role == "owner"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: `AttributeError: 'User' object has no attribute 'role'` (또는 SQLAlchemy가 컬럼 없다고 실패)

- [ ] **Step 3: `schema.sql`의 `users` 테이블에 `role` 컬럼 추가**

```sql
CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    email            VARCHAR(255) UNIQUE,           -- 카카오 전용 계정은 이메일 없을 수 있음 (비즈니스 미인증)
    password_hash    VARCHAR(255),                  -- bcrypt. 카카오 전용 계정은 NULL (이메일 로그인용, 합의 사항)
    nickname         VARCHAR(50)  NOT NULL,
    phone_hash       CHAR(64),                     -- SHA-256 hex. 전화번호 원문 저장 금지
    marketing_agreed BOOLEAN      NOT NULL DEFAULT FALSE,
    role             VARCHAR(10)  NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'admin')),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: `backend/app/models.py`의 `User` 클래스에 `role` 필드 추가**

`marketing_agreed` 필드 바로 다음 줄에 추가:

```python
    role: Mapped[str] = mapped_column(String(10), default="owner")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_admin.py
git commit -m "feat: users.role 컬럼 추가 (owner/admin)"
```

---

## Task 2: `/auth/me` 응답에 `role` 포함

**Files:**
- Modify: `backend/app/routers/auth.py` (`_user_dict` 함수, 74~81번째 줄 부근)
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `User.role` (Task 1)
- Produces: `_user_dict()`가 반환하는 dict에 `"role"` 키 포함 — `/auth/me`, `/auth/login`, `/auth/signup`, `/auth/kakao/callback` 응답 전부에 자동으로 반영된다(넷 다 이 함수를 공유). 프론트가 로그인 응답/`/auth/me`로 관리자 여부를 판단할 때 이 필드를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_admin.py`에 추가:

```python
def test_auth_me_includes_role(client, seeded_user, auth_headers):
    res = client.get("/auth/me", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["role"] == "owner"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py::test_auth_me_includes_role -v`
Expected: `KeyError: 'role'`

- [ ] **Step 3: `_user_dict`에 `role` 추가**

`backend/app/routers/auth.py`의 `_user_dict` 함수를 수정:

```python
def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "has_phone": user.phone_hash is not None,
        "marketing_agreed": user.marketing_agreed,
        "role": user.role,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/auth.py backend/tests/test_admin.py
git commit -m "feat: /auth/me 등 사용자 응답에 role 포함"
```

---

## Task 3: 로그인 브루트포스 방어

**Files:**
- Modify: `backend/app/routers/auth.py` (import 구간 + `login()` 함수, 241~246번째 줄 부근)
- Test: `backend/tests/test_auth_lockout.py` (새 파일)

**Interfaces:**
- Produces: `POST /auth/login`이 같은 이메일로 5회 연속 실패 시 15분간 `429`를 반환. 이 태스크는 이후 태스크와 인터페이스를 공유하지 않는 독립 기능이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_auth_lockout.py` 새로 생성 — 모듈 레벨 실패 카운터가 테스트 간에 새는 것을 막기 위해 매 테스트 전에 초기화하는 autouse fixture를 이 파일에 로컬로 둔다:

```python
import pytest


@pytest.fixture(autouse=True)
def _clear_login_failures():
    from app.routers import auth as auth_router

    auth_router._login_failures.clear()
    yield


def test_login_locks_out_after_five_failures(client, seeded_user):
    for _ in range(5):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 429


def test_login_success_resets_failure_counter(client, seeded_user):
    for _ in range(4):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 200

    # 성공 직후엔 카운터가 리셋됐으니 다시 4번 실패해도 아직 안 잠긴다
    for _ in range(4):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401
    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 200


def test_login_lockout_is_scoped_to_email(client, seeded_user):
    for _ in range(5):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    # 다른 이메일은 잠기지 않는다(존재하지 않는 계정이라 401이지 429가 아님)
    res = client.post("/auth/login", json={"email": "other@example.com", "password": "whatever"})
    assert res.status_code == 401
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_lockout.py -v`
Expected: `AttributeError: module 'app.routers.auth' has no attribute '_login_failures'`

- [ ] **Step 3: `backend/app/routers/auth.py` 상단 import에 `defaultdict` 추가**

파일 최상단 `import hashlib` 다음 줄에:

```python
from collections import defaultdict
```

- [ ] **Step 4: 잠금 상태 저장소 + 헬퍼 함수 추가**

`router = APIRouter(...)` 줄 바로 다음에 추가:

```python
# 프로세스 메모리 내 카운터 — Railway는 이 서비스를 단일 인스턴스로만 띄우므로
# (numReplicas 미지정) 여러 서버 간 카운터 불일치 문제가 없다. 배포/재시작 시
# 초기화되는 건 알려진 한계로 남겨둔다(관리자 페이지 설계 문서 참고).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_WINDOW = timedelta(minutes=15)
_login_failures: dict[str, list[datetime]] = defaultdict(list)


def _check_login_lockout(email: str) -> None:
    now = datetime.now(timezone.utc)
    recent = [t for t in _login_failures[email] if now - t < _LOGIN_LOCKOUT_WINDOW]
    _login_failures[email] = recent
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(429, "너무 많이 실패했어요. 15분 후 다시 시도해주세요")


def _record_login_failure(email: str) -> None:
    _login_failures[email].append(datetime.now(timezone.utc))


def _reset_login_failures(email: str) -> None:
    _login_failures.pop(email, None)
```

- [ ] **Step 5: `login()`에 잠금 체크/기록/리셋 연결**

기존:

```python
@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))
```

변경 후:

```python
@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    _check_login_lockout(body.email)
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        _record_login_failure(body.email)
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    _reset_login_failures(body.email)
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_lockout.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: 기존 인증 테스트가 안 깨졌는지 전체 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v -k auth`
Expected: 전부 PASS (기존 로그인 관련 테스트들이 이 변경으로 깨지지 않아야 함)

- [ ] **Step 8: 커밋**

```bash
git add backend/app/routers/auth.py backend/tests/test_auth_lockout.py
git commit -m "feat: 로그인 5회 연속 실패 시 15분 잠금(브루트포스 방어)"
```

---

## Task 4: `require_admin` 의존성 + `GET /admin/payments`

**Files:**
- Modify: `backend/app/auth.py` (`require_admin` 함수 추가)
- Create: `backend/app/routers/admin.py`
- Modify: `backend/app/main.py` (라우터 등록)
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `User.role`(Task 1), `get_current_user`(기존)
- Produces: `require_admin` FastAPI 의존성(`backend/app/auth.py`) — 이후 모든 `/admin/*` 엔드포인트가 이걸 쓴다. `admin.router`(`backend/app/routers/admin.py`) — 이후 태스크들이 이 파일에 엔드포인트를 계속 추가한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_admin.py`에 추가(`datetime`/`timezone`은 Task 1에서 이미 import돼 있다 — `date`만 새로 추가):

```python
from datetime import date

from app.models import Payment, User


def _promote_to_admin(db_session, user: User) -> None:
    user.role = "admin"
    db_session.commit()


def test_admin_payments_requires_admin_role(client, seeded_user, auth_headers):
    res = client.get("/admin/payments", headers=auth_headers)
    assert res.status_code == 403


def test_admin_payments_lists_recent_payments(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    db_session.add(Payment(
        user_id=seeded_user["user"].id, order_id="order-1", plan="pro", amount=19900,
        status="approved", requested_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.get("/admin/payments", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-1"
    assert body[0]["user_email"] == "demo@dris.kr"
    assert body[0]["status"] == "approved"


def test_admin_payments_filters_by_status(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    db_session.add_all([
        Payment(user_id=seeded_user["user"].id, order_id="order-a", plan="pro", amount=19900,
                status="approved", requested_at=datetime.now(timezone.utc)),
        Payment(user_id=seeded_user["user"].id, order_id="order-b", plan="pro", amount=19900,
                status="failed", requested_at=datetime.now(timezone.utc), fail_reason="카드 한도 초과"),
    ])
    db_session.commit()

    res = client.get("/admin/payments?status=failed", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-b"
    assert body[0]["fail_reason"] == "카드 한도 초과"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_payments`
Expected: `404 Not Found` (라우트 자체가 없음)

- [ ] **Step 3: `backend/app/auth.py`에 `require_admin` 추가**

파일 맨 끝(`get_user_default_store_id` 함수 다음)에 추가:

```python
def require_admin(user: User = Depends(get_current_user)) -> User:
    """관리자 전용 라우트에서 쓰는 FastAPI dependency. require_pro_plan과 같은 패턴 —
    프론트에서만 막으면 개발자도구로 백엔드를 직접 두드려 우회할 수 있으므로 백엔드에서도
    강제해야 한다."""
    if user.role != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user
```

- [ ] **Step 4: `backend/app/routers/admin.py` 새로 생성**

```python
"""관리자 전용 엔드포인트 — 결제 이력 조회, 배민 연결 매장 운영 현황, 유저 조회+플랜
수동 변경. require_admin으로 전부 보호된다. 설계 배경은
docs/superpowers/specs/2026-09-01-admin-panel-design.md 참고."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import Payment, User

router = APIRouter(tags=["admin"])


@router.get("/admin/payments")
def admin_list_payments(
    status: str | None = None,
    limit: int = 50,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(Payment).order_by(Payment.requested_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Payment.status == status)
    payments = db.scalars(query).all()

    rows = []
    for p in payments:
        user = db.get(User, p.user_id)
        rows.append({
            "order_id": p.order_id,
            "user_email": user.email,
            "user_nickname": user.nickname,
            "plan": p.plan,
            "amount": p.amount,
            "status": p.status,
            "requested_at": p.requested_at,
            "approved_at": p.approved_at,
            "fail_reason": p.fail_reason,
        })
    return rows
```

- [ ] **Step 5: `backend/app/main.py`에 라우터 등록**

import 줄 수정(8번째 줄):

```python
from app.routers import admin, ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections
```

`app.include_router(billing.router)` 다음 줄에 추가:

```python
app.include_router(admin.router)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/auth.py backend/app/routers/admin.py backend/app/main.py backend/tests/test_admin.py
git commit -m "feat: require_admin 의존성 + GET /admin/payments"
```

---

## Task 5: `GET /admin/stores` — 매장 운영 현황

**Files:**
- Modify: `backend/app/routers/admin.py`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `require_admin`(Task 4)
- Produces: `GET /admin/stores` — 이후 Task 6(자동답글 토글)이 같은 목록 화면에서 쓰는 매장별 행 구조를 이어서 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_admin.py`에 추가:

```python
from app.models import ReplySetting, ReviewSyncJob, Store, StorePlatformConnection


def test_admin_stores_only_includes_baemin_connections_with_credentials(
    client, db_session, seeded_user, platforms, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    # seeded_user가 이미 만들어둔 연결은 credential_ciphertext가 NULL이라 제외돼야 한다
    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == []


def test_admin_stores_includes_latest_sync_job_and_auto_reply_state(
    client, db_session, seeded_user, platforms, reply_styles, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    conn = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == store.id)
    )
    conn.credential_ciphertext = "encrypted-blob"
    db_session.add(ReplySetting(
        store_id=store.id, style_id=reply_styles.id, auto_reply_enabled=True, auto_reply_min_rating=5,
    ))
    db_session.add(ReviewSyncJob(
        store_id=store.id, platform_id=platforms["baemin"].id, status="success", triggered_by="scheduled",
        reviews_fetched=3, reviews_inserted=1,
        started_at=datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 30, 4, 5, tzinfo=timezone.utc),
    ))
    # 더 최근 실패 잡 — 이게 "최신"으로 선택돼야 한다
    db_session.add(ReviewSyncJob(
        store_id=store.id, platform_id=platforms["baemin"].id, status="failed", triggered_by="manual",
        error_message="매장 목록을 확인하지 못했습니다",
        started_at=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()

    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    row = body[0]
    assert row["store_name"] == store.name
    assert row["owner_email"] == "demo@dris.kr"
    assert row["auto_reply_enabled"] is True
    assert row["last_sync"]["status"] == "failed"
    assert row["last_sync"]["triggered_by"] == "manual"
    assert row["last_sync"]["error_message"] == "매장 목록을 확인하지 못했습니다"


def test_admin_stores_handles_store_with_no_sync_history(
    client, db_session, seeded_user, platforms, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]
    conn = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == store.id)
    )
    conn.credential_ciphertext = "encrypted-blob"
    db_session.commit()

    res = client.get("/admin/stores", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["last_sync"] is None
    assert body[0]["auto_reply_enabled"] is False  # reply_settings 행이 아예 없을 때의 기본값
```

이 테스트들은 `select`를 직접 쓴다 — `test_admin.py` 맨 위 import 구간에 다음 줄을 추가한다(아직 없다면):

```python
from sqlalchemy import select
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_stores`
Expected: `404 Not Found`

- [ ] **Step 3: `backend/app/routers/admin.py`에 엔드포인트 추가**

파일 상단 import를 확장:

```python
from app.models import Payment, Platform, ReplySetting, ReviewSyncJob, Store, StorePlatformConnection, User
```

`admin_list_payments` 함수 다음에 추가:

```python
@router.get("/admin/stores")
def admin_list_stores(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    baemin = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if baemin is None:
        return []

    conns = db.scalars(
        select(StorePlatformConnection).where(
            StorePlatformConnection.platform_id == baemin.id,
            StorePlatformConnection.credential_ciphertext.is_not(None),
        )
    ).all()

    rows = []
    for conn in conns:
        store = db.get(Store, conn.store_id)
        owner = db.get(User, store.user_id)
        latest_job = db.scalar(
            select(ReviewSyncJob)
            .where(ReviewSyncJob.store_id == store.id)
            .order_by(ReviewSyncJob.started_at.desc())
            .limit(1)
        )
        rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == store.id))

        rows.append({
            "store_id": store.id,
            "store_name": store.name,
            "owner_email": owner.email,
            "owner_nickname": owner.nickname,
            "last_sync": None if latest_job is None else {
                "triggered_by": latest_job.triggered_by,
                "status": latest_job.status,
                "started_at": latest_job.started_at,
                "finished_at": latest_job.finished_at,
                "error_message": latest_job.error_message,
            },
            "auto_reply_enabled": rs.auto_reply_enabled if rs is not None else False,
        })
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_stores`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_admin.py
git commit -m "feat: GET /admin/stores — 배민 연결 매장 동기화 상태+자동답글 조회"
```

---

## Task 6: `PATCH /admin/stores/{store_id}/auto-reply`

**Files:**
- Modify: `backend/app/routers/admin.py`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `require_admin`(Task 4), `ReplySetting`(Task 5에서 이미 import됨)
- Produces: `PATCH /admin/stores/{store_id}/auto-reply` — 프론트 매장 운영 현황 화면의 토글 액션이 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_admin_toggle_auto_reply_updates_setting(
    client, db_session, seeded_user, reply_styles, auth_headers,
):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]
    rs = ReplySetting(store_id=store.id, style_id=reply_styles.id, auto_reply_enabled=True, auto_reply_min_rating=5)
    db_session.add(rs)
    db_session.commit()

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": False}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is False

    db_session.refresh(rs)
    assert rs.auto_reply_enabled is False


def test_admin_toggle_auto_reply_404_when_no_settings(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    store = seeded_user["store"]

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": True}, headers=auth_headers,
    )
    assert res.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k toggle_auto_reply`
Expected: `404` 테스트는 우연히 통과할 수 있으나(라우트 자체가 없어도 404), 첫 번째 테스트는 `assert res.status_code == 200`에서 실패

- [ ] **Step 3: 엔드포인트 추가**

파일 상단에 `from pydantic import BaseModel` 추가. `admin_list_stores` 함수 다음에 추가:

```python
class AutoReplyToggleRequest(BaseModel):
    enabled: bool


@router.patch("/admin/stores/{store_id}/auto-reply")
def admin_toggle_auto_reply(
    store_id: int,
    body: AutoReplyToggleRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == store_id))
    if rs is None:
        raise HTTPException(404, "답글 설정이 없습니다")
    rs.auto_reply_enabled = body.enabled
    db.commit()
    return {"store_id": store_id, "auto_reply_enabled": rs.auto_reply_enabled}
```

`from fastapi import APIRouter, Depends` 줄을 `from fastapi import APIRouter, Depends, HTTPException`로 수정한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k toggle_auto_reply`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_admin.py
git commit -m "feat: PATCH /admin/stores/{id}/auto-reply — 관리자 자동답글 긴급 스위치"
```

---

## Task 7: `GET /admin/users` — 유저 검색

**Files:**
- Modify: `backend/app/routers/admin.py`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `require_admin`(Task 4), `effective_plan`(`app.plan`, 기존)
- Produces: `GET /admin/users?q=` — Task 8(플랜 변경)이 같은 화면에서 이어서 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_admin_users_search_by_email_or_nickname(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    other = User(
        email="another@example.com", nickname="다른사장",
        password_hash="x", marketing_agreed=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.commit()

    res = client.get("/admin/users?q=demo", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["email"] == "demo@dris.kr"
    assert body[0]["plan"] == "basic"
    assert body[0]["store_count"] == 1


def test_admin_users_no_query_returns_recent_users(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.get("/admin/users", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json()) >= 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_users`
Expected: `404 Not Found`

- [ ] **Step 3: 엔드포인트 추가**

파일 상단 import에 추가:

```python
from sqlalchemy import func, or_, select

from app.models import Payment, Platform, ReplySetting, ReviewSyncJob, Store, StorePlatformConnection, Subscription, User
from app.plan import effective_plan
```

(기존 `from sqlalchemy import select` 줄과 `from app.models import ...` 줄을 위 내용으로 각각 교체)

`admin_toggle_auto_reply` 함수 다음에 추가:

```python
@router.get("/admin/users")
def admin_list_users(
    q: str | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(User).order_by(User.created_at.desc()).limit(50)
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.email.ilike(like), User.nickname.ilike(like)))
    users = db.scalars(query).all()

    rows = []
    for u in users:
        sub = db.scalar(select(Subscription).where(Subscription.user_id == u.id))
        store_count = db.scalar(select(func.count(Store.id)).where(Store.user_id == u.id)) or 0
        rows.append({
            "user_id": u.id,
            "email": u.email,
            "nickname": u.nickname,
            "created_at": u.created_at,
            "plan": effective_plan(sub),
            "expires_at": sub.expires_at if sub else None,
            "store_count": store_count,
        })
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_users`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_admin.py
git commit -m "feat: GET /admin/users — 이메일/닉네임 검색 + 현재 플랜 조회"
```

---

## Task 8: `PATCH /admin/users/{user_id}/plan` — 플랜 수동 변경

**Files:**
- Modify: `backend/app/routers/admin.py`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `require_admin`(Task 4), `kst_today`(`app.plan`, 기존)
- Produces: `PATCH /admin/users/{user_id}/plan` — 유저 관리 화면의 플랜 변경 액션이 호출하는 마지막 엔드포인트.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import timedelta


def test_admin_set_plan_to_pro_sets_expires_at_from_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 14},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "pro"
    assert body["expires_at"] == str(date.today() + timedelta(days=14))


def test_admin_set_plan_to_pro_defaults_to_30_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro"}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["expires_at"] == str(date.today() + timedelta(days=30))


def test_admin_set_plan_to_basic_clears_expires_at(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro", "days": 30}, headers=auth_headers,
    )

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "basic"}, headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["plan"] == "basic"
    assert res.json()["expires_at"] is None


def test_admin_set_plan_rejects_out_of_range_days(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 400},
        headers=auth_headers,
    )
    assert res.status_code == 422


def test_admin_set_plan_404_for_unknown_user(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch("/admin/users/999999/plan", json={"plan": "pro"}, headers=auth_headers)
    assert res.status_code == 404


def test_admin_set_plan_rejects_invalid_plan_value(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "enterprise"}, headers=auth_headers,
    )
    assert res.status_code == 422
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_set_plan`
Expected: `404 Not Found` (라우트 없음)

- [ ] **Step 3: 엔드포인트 추가**

파일 상단 import 수정:

```python
from datetime import timedelta
from typing import Literal

from app.plan import effective_plan, kst_today
```

`admin_list_users` 함수 다음에 추가:

```python
class AdminPlanUpdateRequest(BaseModel):
    plan: Literal["basic", "pro"]
    days: int | None = None


@router.patch("/admin/users/{user_id}/plan")
def admin_set_plan(
    user_id: int,
    body: AdminPlanUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")

    days = body.days if body.days is not None else 30
    if body.plan == "pro" and not (1 <= days <= 365):
        raise HTTPException(422, "days는 1~365 사이여야 합니다")

    sub = db.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if sub is None:
        sub = Subscription(user_id=user_id, plan="basic", daily_reply_limit=10, started_at=kst_today())
        db.add(sub)
        db.flush()

    if body.plan == "pro":
        sub.plan = "pro"
        sub.expires_at = kst_today() + timedelta(days=days)
    else:
        sub.plan = "basic"
        sub.expires_at = None

    db.commit()
    return {"user_id": user_id, "plan": sub.plan, "expires_at": sub.expires_at}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v -k admin_set_plan`
Expected: PASS (6 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 확인**

Run: `cd backend && .venv/bin/python -m pytest -v`
Expected: 전부 PASS (기존 테스트가 이번 변경들로 깨지지 않아야 함)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_admin.py
git commit -m "feat: PATCH /admin/users/{id}/plan — 관리자 플랜 수동 변경"
```

---

## Task 9: 관리자 엔드포인트 5개 전부 비관리자 거부 확인

**Files:**
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: Task 4~8에서 만든 5개 엔드포인트 전부

이 태스크는 새 프로덕션 코드를 추가하지 않는다 — 지금까지 만든 5개 엔드포인트가 전부 일관되게 403을 내는지 한 번에 확인하는 회귀 테스트만 추가한다(Task 4에서 결제 조회 하나만 개별로 확인했으니, 나머지 4개도 같은 계약을 지키는지 파라미터화로 마저 확인).

- [ ] **Step 1: 테스트 작성**

```python
import pytest


@pytest.mark.parametrize("path", ["/admin/payments", "/admin/stores", "/admin/users"])
def test_admin_get_endpoints_reject_non_admin(client, seeded_user, auth_headers, path):
    res = client.get(path, headers=auth_headers)
    assert res.status_code == 403


def test_admin_toggle_auto_reply_rejects_non_admin(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/stores/{seeded_user['store'].id}/auto-reply",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert res.status_code == 403


def test_admin_set_plan_rejects_non_admin(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro"},
        headers=auth_headers,
    )
    assert res.status_code == 403
```

- [ ] **Step 2: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: 전부 PASS (지금까지 만든 admin 라우트들이 `require_admin`을 빠짐없이 쓰고 있다는 걸 한 번에 증명)

- [ ] **Step 3: 커밋**

```bash
git add backend/tests/test_admin.py
git commit -m "test: 관리자 엔드포인트 5개 비관리자 거부 회귀 테스트"
```

---

## Task 10: 프론트 — `Toggle` 컴포넌트

**Files:**
- Create: `frontend/src/components/Toggle.tsx`

**Interfaces:**
- Produces: `<Toggle checked={boolean} onChange={(next: boolean) => void} disabled?={boolean} />` — Task 14(매장 운영 현황 페이지)가 이걸 자동답글 스위치로 쓴다.

- [ ] **Step 1: 컴포넌트 작성**

```tsx
"use client";

export function Toggle({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? "bg-accent" : "bg-surface-2 border border-border-subtle"
      }`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
          checked ? "translate-x-5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(아직 아무도 이 컴포넌트를 안 쓰므로 미사용 경고도 없어야 함 — export된 컴포넌트라 unused 경고 대상 아님)

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/components/Toggle.tsx
git commit -m "feat: 재사용 가능한 Toggle 스위치 컴포넌트 추가"
```

---

## Task 11: 프론트 — 로그인 후 관리자 리다이렉트

**Files:**
- Modify: `frontend/src/app/login/page.tsx`

**Interfaces:**
- Consumes: `/auth/login` 응답의 `user.role`(Task 2)

- [ ] **Step 1: `TokenResponse` 타입에 `user.role` 추가하고 리다이렉트 분기**

기존:

```tsx
type TokenResponse = { access_token: string };
```

변경 후:

```tsx
type TokenResponse = { access_token: string; user: { role: string } };
```

`submit` 함수 안의 기존:

```tsx
      setToken(res.access_token);
      router.push("/dashboard");
```

변경 후:

```tsx
      setToken(res.access_token);
      router.push(res.user.role === "admin" ? "/ops-4k9x2m" : "/dashboard");
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/app/login/page.tsx
git commit -m "feat: 로그인 성공 시 관리자는 /ops-4k9x2m로 리다이렉트"
```

---

## Task 12: 프론트 — 관리자 레이아웃 + 사이드바 + 경로 가드

**Files:**
- Create: `frontend/src/components/AdminSidebar.tsx`
- Create: `frontend/src/app/(admin)/ops-4k9x2m/layout.tsx`

**Interfaces:**
- Consumes: `/auth/me` 응답의 `role`(Task 2), 기존 `Logo` 컴포넌트, `clearToken`/`getToken`(`@/lib/api`)
- Produces: 이 레이아웃 아래 배치되는 모든 페이지(Task 13~15)는 이미 관리자임이 보장된 상태에서 렌더링된다 — 각 페이지는 별도로 role 체크를 하지 않아도 된다.

- [ ] **Step 1: `AdminSidebar` 작성**

```tsx
"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Logo } from "@/components/Logo";
import { clearToken } from "@/lib/api";

const NAV = [
  { href: "/ops-4k9x2m/payments", label: "결제 이력" },
  { href: "/ops-4k9x2m/stores", label: "매장 운영 현황" },
  { href: "/ops-4k9x2m/users", label: "유저 관리" },
];

export function AdminSidebar() {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col overflow-y-auto border-r border-border-subtle bg-surface">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <Logo size={32} />
        <div>
          <p className="text-sm font-semibold leading-tight">스토어 타겟</p>
          <p className="text-[11px] text-muted leading-tight">관리자</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3 pb-4">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-lg px-3 py-2.5 text-sm transition ${
                active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border-subtle px-4 py-4 space-y-2">
        <Link
          href="/dashboard"
          className="block text-center text-xs text-muted hover:text-foreground"
        >
          사장님 화면으로 돌아가기
        </Link>
        <button
          onClick={() => {
            clearToken();
            router.replace("/login");
          }}
          className="w-full rounded-lg border border-border-subtle py-2 text-xs text-muted transition hover:border-danger hover:text-danger"
        >
          로그아웃
        </button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: 레이아웃(경로 가드) 작성**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AdminSidebar } from "@/components/AdminSidebar";
import { apiGet, getToken } from "@/lib/api";

type MeResponse = { role: string };

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    apiGet<MeResponse>("/auth/me")
      .then((me) => {
        if (me.role !== "admin") {
          router.replace("/dashboard");
          return;
        }
        setReady(true);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  if (!ready) {
    return (
      <div className="flex h-screen w-full items-center justify-center text-sm text-muted">
        불러오는 중...
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <AdminSidebar />
      <main className="flex-1 overflow-y-auto px-8 py-8">{children}</main>
    </div>
  );
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(아직 이 레이아웃 아래 페이지가 없어 Next.js가 라우트로 인식하지 않을 수 있음 — Task 13에서 첫 페이지를 추가하면 실제 라우트가 됨)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/AdminSidebar.tsx "frontend/src/app/(admin)/ops-4k9x2m/layout.tsx"
git commit -m "feat: 관리자 레이아웃(사이드바+role 가드) 추가"
```

---

## Task 13: 프론트 — 결제 이력 화면

**Files:**
- Create: `frontend/src/app/(admin)/ops-4k9x2m/payments/page.tsx`

**Interfaces:**
- Consumes: `GET /admin/payments`(Task 4), `AdminLayout`(Task 12), 기존 `won`/`apiGet`(`@/lib/api`)

- [ ] **Step 1: 페이지 작성**

```tsx
"use client";

import { useEffect, useState } from "react";
import { apiGet, won } from "@/lib/api";

type PaymentRow = {
  order_id: string;
  user_email: string | null;
  user_nickname: string;
  plan: string;
  amount: number;
  status: "pending" | "approved" | "failed";
  requested_at: string;
  approved_at: string | null;
  fail_reason: string | null;
};

const STATUS_LABEL: Record<PaymentRow["status"], { label: string; className: string }> = {
  pending: { label: "대기중", className: "text-warning" },
  approved: { label: "승인됨", className: "text-success" },
  failed: { label: "실패", className: "text-danger" },
};

export default function AdminPaymentsPage() {
  const [rows, setRows] = useState<PaymentRow[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const query = statusFilter ? `?status=${statusFilter}` : "";
    apiGet<PaymentRow[]>(`/admin/payments${query}`)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [statusFilter]);

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">결제 이력</h1>

      <div className="flex gap-2">
        {["", "pending", "approved", "failed"].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              statusFilter === s ? "bg-accent text-white" : "border border-border-subtle text-muted hover:text-foreground"
            }`}
          >
            {s === "" ? "전체" : STATUS_LABEL[s as PaymentRow["status"]].label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-border-subtle bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border-subtle text-xs text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">사용자</th>
                <th className="px-4 py-3 font-medium">플랜</th>
                <th className="px-4 py-3 font-medium">금액</th>
                <th className="px-4 py-3 font-medium">상태</th>
                <th className="px-4 py-3 font-medium">요청 시각</th>
                <th className="px-4 py-3 font-medium">실패 사유</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.order_id} className="border-b border-border-subtle last:border-0">
                  <td className="px-4 py-3">
                    <p>{r.user_nickname}</p>
                    <p className="text-xs text-muted">{r.user_email ?? "카카오 계정"}</p>
                  </td>
                  <td className="px-4 py-3">{r.plan}</td>
                  <td className="px-4 py-3">{won(r.amount)}</td>
                  <td className={`px-4 py-3 font-medium ${STATUS_LABEL[r.status].className}`}>
                    {STATUS_LABEL[r.status].label}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">{new Date(r.requested_at).toLocaleString("ko-KR")}</td>
                  <td className="px-4 py-3 text-xs text-muted">{r.fail_reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add "frontend/src/app/(admin)/ops-4k9x2m/payments/page.tsx"
git commit -m "feat: 관리자 결제 이력 화면"
```

---

## Task 14: 프론트 — 매장 운영 현황 화면

**Files:**
- Create: `frontend/src/app/(admin)/ops-4k9x2m/stores/page.tsx`

**Interfaces:**
- Consumes: `GET /admin/stores`, `PATCH /admin/stores/{id}/auto-reply`(Task 5, 6), `Toggle`(Task 10)

- [ ] **Step 1: 페이지 작성**

```tsx
"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPatch } from "@/lib/api";
import { Toggle } from "@/components/Toggle";

type SyncStatus = "pending" | "running" | "success" | "failed";

type StoreRow = {
  store_id: number;
  store_name: string;
  owner_email: string | null;
  owner_nickname: string;
  last_sync: {
    triggered_by: "manual" | "scheduled";
    status: SyncStatus;
    started_at: string;
    finished_at: string | null;
    error_message: string | null;
  } | null;
  auto_reply_enabled: boolean;
};

const SYNC_STATUS_LABEL: Record<SyncStatus, { label: string; className: string }> = {
  pending: { label: "대기중", className: "text-muted" },
  running: { label: "진행중", className: "text-accent" },
  success: { label: "성공", className: "text-success" },
  failed: { label: "실패", className: "text-danger" },
};

export default function AdminStoresPage() {
  const [rows, setRows] = useState<StoreRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    apiGet<StoreRow[]>("/admin/stores")
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleToggle = async (storeId: number, next: boolean) => {
    setTogglingId(storeId);
    try {
      await apiPatch(`/admin/stores/${storeId}/auto-reply`, { enabled: next });
      setRows((prev) => prev.map((r) => (r.store_id === storeId ? { ...r, auto_reply_enabled: next } : r)));
    } catch {
      alert("변경에 실패했어요. 다시 시도해주세요.");
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">매장 운영 현황</h1>
      <p className="text-xs text-muted">배민 실계정이 연결된 매장만 표시됩니다.</p>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <div key={r.store_id} className="rounded-2xl border border-border-subtle bg-surface p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold">{r.store_name}</p>
                  <p className="text-xs text-muted">{r.owner_nickname} · {r.owner_email ?? "카카오 계정"}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">자동답글</span>
                  <Toggle
                    checked={r.auto_reply_enabled}
                    onChange={(next) => handleToggle(r.store_id, next)}
                    disabled={togglingId === r.store_id}
                  />
                </div>
              </div>

              <div className="mt-3 border-t border-border-subtle pt-3 text-xs">
                {r.last_sync === null ? (
                  <p className="text-muted">동기화 기록 없음</p>
                ) : (
                  <>
                    <p className={SYNC_STATUS_LABEL[r.last_sync.status].className}>
                      {SYNC_STATUS_LABEL[r.last_sync.status].label}
                      {" · "}
                      {r.last_sync.triggered_by === "manual" ? "수동" : "자동"}
                      {" · "}
                      {new Date(r.last_sync.finished_at ?? r.last_sync.started_at).toLocaleString("ko-KR")}
                    </p>
                    {r.last_sync.error_message && (
                      <p className="mt-1 text-danger">{r.last_sync.error_message}</p>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add "frontend/src/app/(admin)/ops-4k9x2m/stores/page.tsx"
git commit -m "feat: 관리자 매장 운영 현황 화면 (동기화 상태+자동답글 스위치)"
```

---

## Task 15: 프론트 — 유저 관리 화면

**Files:**
- Create: `frontend/src/app/(admin)/ops-4k9x2m/users/page.tsx`

**Interfaces:**
- Consumes: `GET /admin/users`, `PATCH /admin/users/{id}/plan`(Task 7, 8)

- [ ] **Step 1: 페이지 작성**

```tsx
"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPatch } from "@/lib/api";

type UserRow = {
  user_id: number;
  email: string | null;
  nickname: string;
  created_at: string;
  plan: "basic" | "pro";
  expires_at: string | null;
  store_count: number;
};

export default function AdminUsersPage() {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [daysInputs, setDaysInputs] = useState<Record<number, string>>({});
  const [savingId, setSavingId] = useState<number | null>(null);

  const load = (q: string) => {
    setLoading(true);
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    apiGet<UserRow[]>(`/admin/users${qs}`)
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(() => load(""), []);

  const changePlan = async (userId: number, plan: "basic" | "pro") => {
    setSavingId(userId);
    try {
      const days = plan === "pro" ? Number(daysInputs[userId] || "30") : undefined;
      const updated = await apiPatch<{ plan: "basic" | "pro"; expires_at: string | null }>(
        `/admin/users/${userId}/plan`,
        { plan, days },
      );
      setRows((prev) =>
        prev.map((r) => (r.user_id === userId ? { ...r, plan: updated.plan, expires_at: updated.expires_at } : r)),
      );
    } catch {
      alert("플랜 변경에 실패했어요. 다시 시도해주세요.");
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">유저 관리</h1>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          load(query);
        }}
        className="flex gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="이메일 또는 닉네임 검색"
          className="w-full max-w-xs rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button type="submit" className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          검색
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <div key={r.user_id} className="rounded-2xl border border-border-subtle bg-surface p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold">{r.nickname}</p>
                  <p className="text-xs text-muted">
                    {r.email ?? "카카오 계정"} · 가입일 {new Date(r.created_at).toLocaleDateString("ko-KR")} · 매장 {r.store_count}개
                  </p>
                </div>
                <div className="text-right">
                  <p className={`text-sm font-semibold ${r.plan === "pro" ? "text-accent" : "text-foreground"}`}>
                    {r.plan === "pro" ? "Pro" : "Basic"}
                  </p>
                  {r.expires_at && <p className="text-xs text-muted">~{r.expires_at}</p>}
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2 border-t border-border-subtle pt-3">
                <input
                  type="number"
                  min={1}
                  max={365}
                  placeholder="30"
                  value={daysInputs[r.user_id] ?? ""}
                  onChange={(e) => setDaysInputs((prev) => ({ ...prev, [r.user_id]: e.target.value }))}
                  className="w-20 rounded-lg border border-border-subtle bg-surface-2 px-2 py-1.5 text-xs outline-none focus:border-accent"
                />
                <span className="text-xs text-muted">일간 Pro 부여</span>
                <button
                  onClick={() => changePlan(r.user_id, "pro")}
                  disabled={savingId === r.user_id}
                  className="rounded-lg border border-accent px-3 py-1.5 text-xs text-accent transition hover:bg-accent-soft disabled:opacity-50"
                >
                  Pro로 변경
                </button>
                <button
                  onClick={() => changePlan(r.user_id, "basic")}
                  disabled={savingId === r.user_id}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  Basic으로 변경
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add "frontend/src/app/(admin)/ops-4k9x2m/users/page.tsx"
git commit -m "feat: 관리자 유저 관리 화면 (검색+플랜 수동 변경)"
```

---

## Task 16: 로컬 수동 검증 + 프로덕션 반영 메모

**Files:** 없음(검증 + 운영 절차만)

- [ ] **Step 1: 전체 테스트 재확인**

```bash
cd backend && .venv/bin/python -m pytest -v
cd ../frontend && npx tsc --noEmit
```

Expected: 둘 다 클린.

- [ ] **Step 2: 로컬 DB에 스키마 반영**

로컬 Postgres에 `role` 컬럼을 실제로 추가한다(로컬 DB는 `schema.sql`을 매번 새로 밀어넣는 게 아니라 기존 데이터가 있는 컨테이너를 계속 쓰는 경우가 많으므로):

```bash
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c \
  "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(10) NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'admin'));"
```

(로컬 DB 컨테이너 이름이 다르면 그에 맞게 바꾼다.)

- [ ] **Step 3: 로컬에서 본인 계정을 관리자로 승격해 브라우저로 직접 확인**

```bash
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c \
  "UPDATE users SET role = 'admin' WHERE email = 'demo@dris.kr';"
```

로컬 프론트(`npm run dev`)에서 데모 계정으로 로그인 → `/ops-4k9x2m`로 자동 리다이렉트되는지, 결제/매장/유저 3개 화면이 실제로 렌더링되는지, 자동답글 토글과 플랜 변경 버튼이 실제로 동작하는지 확인한다. 확인이 끝나면 로컬 데모 계정은 다시 `owner`로 되돌린다(데모 계정 자체는 관리자가 아니어야 하므로):

```bash
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c \
  "UPDATE users SET role = 'owner' WHERE email = 'demo@dris.kr';"
```

- [ ] **Step 4: 프로덕션 반영 (배포 + 수동 SQL, 이 계획 범위 밖 — 별도 승인 후 진행)**

이 플랜을 다 구현하고 나면, 실제 배포는 별도로 사용자 승인을 받고 진행한다:
1. `git push` 후 Railway 백엔드/프론트 재배포
2. Railway 프로덕션 Postgres에 SSH 터널로 접속해 `ALTER TABLE users ADD COLUMN role ...` 실행(Step 2와 동일한 SQL)
3. 본인 계정만 `UPDATE users SET role = 'admin' WHERE email = '...'`로 승격(데모 계정은 승격하지 않음 — 데모 계정은 과제 심사자 등 제3자가 로그인할 수 있으므로 절대 admin으로 두지 않는다)
