# 관리자 패널 완전 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 패널(결제 이력/매장 운영 현황/유저 관리/LLMOps)을 사장님
SaaS 프론트엔드에서 완전히 떼어내 별도 Next.js 앱(`admin/`)으로 분리하고,
사장님 회원 체계와 무관한 단일 비밀번호 인증으로 교체한다.

**Architecture:** `backend/`는 그대로 유지하되 admin 엔드포인트의 인증을
User.role 체크에서 "admin 전용 JWT(DB 조회 없음)" 체크로 바꾼다. `admin/`은
`frontend/`와 같은 레벨의 완전히 독립된 Next.js 앱으로, 비밀번호 로그인 →
기존 `/admin/*`·`/admin/llmops/*` 엔드포인트를 그대로 호출한다. 마지막 단계에서
`frontend`의 기존 `/ops-4k9x2m` 라우트를 삭제한다.

**Tech Stack:** FastAPI + PyJWT(백엔드, 기존 의존성 재사용), Next.js 16 +
React 19 + Tailwind v4(admin 앱, frontend와 동일 버전).

## Global Constraints

- 백엔드는 새 Railway 서비스를 만들지 않는다 — 기존 `backend` 서비스 하나만
  유지한다. (설계 문서 "아키텍처" 절)
- admin 전용 인증은 사장님 회원(`users` 테이블)과 완전히 무관하다 — 단일
  비밀번호(`ADMIN_PASSWORD`) + 별도 JWT 시크릿(`ADMIN_JWT_SECRET`), DB 조회
  없음. (설계 문서 "인증" 절)
- `users.role` 컬럼/CHECK 제약은 그대로 둔다 — 이번 작업에서 삭제하지
  않는다. (설계 문서 "인증" 절)
- 경로 매핑: `/ops-4k9x2m` → `/`(payments로 리다이렉트), `/ops-4k9x2m/payments`
  → `/payments`, `/ops-4k9x2m/stores` → `/stores`, `/ops-4k9x2m/users` →
  `/users`, `/ops-4k9x2m/llmops` → `/llmops`. (설계 문서 "화면 구성" 절)
- CORS: 백엔드에 `ADMIN_FRONTEND_ORIGIN` 환경변수를 추가해 `allow_origins`에
  포함한다 — 기존 `FRONTEND_ORIGIN`과 둘 다 허용. (설계 문서 "데이터 흐름" 절)
- `admin/`은 `frontend/`, `backend/`와 같은 레벨의 완전히 독립된 Next.js
  프로젝트다(자체 `package.json`/`tsconfig.json`/`next.config.ts`/
  `railway.json`). (설계 문서 "아키텍처" 절)
- 기존 `/ops-4k9x2m` 라우트와 `require_admin`(User.role 체크)은 새 체계가
  동작 확인된 뒤 **마지막 단계**에서 삭제한다 — 중간 상태로 끝내지 않는다.
  (설계 문서 "전환 순서" 절)
- Railway 서비스 생성/환경변수 설정/실배포는 이 플랜의 태스크가 아니다 —
  코드가 전부 완성·테스트된 뒤 별도로(이 세션의 코디네이터가 직접) 설계
  문서 "배포 순서" 절을 따라 진행한다.

---

### Task 1: 백엔드 — admin 전용 인증(admin-auth 로그인 + require_admin_token)

**Files:**
- Create: `backend/app/routers/admin_auth.py`
- Modify: `backend/app/main.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_admin_auth.py`

**Interfaces:**
- Consumes: 없음(이 태스크가 admin 인증의 시작점).
- Produces: `app.routers.admin_auth.router`(FastAPI `APIRouter`, `POST
  /admin-auth/login` 하나를 가짐), `app.routers.admin_auth.
  require_admin_token`(파라미터 없이 `Depends`로 쓰는 FastAPI dependency,
  반환형 `None`, 실패 시 401/403 `HTTPException`), `app.routers.admin_auth.
  ADMIN_PASSWORD`/`ADMIN_JWT_SECRET`(모듈 레벨 문자열 상수, 테스트에서
  `monkeypatch.setattr`로 덮어쓸 수 있음) — Task 2가 `require_admin_token`을
  가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_admin_auth.py` 파일을 아래 내용으로 만든다.

```python
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.routers import admin_auth


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setattr(admin_auth, "ADMIN_JWT_SECRET", "test-admin-secret")


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_auth.router)

    @app.get("/protected")
    def protected(_: None = Depends(admin_auth.require_admin_token)):
        return {"ok": True}

    return app


@pytest.fixture()
def admin_client():
    return TestClient(_make_test_app())


def test_login_with_correct_password_returns_token(admin_client):
    res = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"})
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_login_with_wrong_password_returns_401(admin_client):
    res = admin_client.post("/admin-auth/login", json={"password": "wrong"})
    assert res.status_code == 401


def test_protected_route_accepts_valid_admin_token(admin_client):
    token = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"}).json()["access_token"]
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_protected_route_rejects_missing_token(admin_client):
    res = admin_client.get("/protected")
    assert res.status_code == 401


def test_protected_route_rejects_token_signed_with_wrong_secret(admin_client):
    forged = jwt.encode({"admin": True}, "not-the-real-secret", algorithm="HS256")
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_protected_route_rejects_token_without_admin_claim(admin_client):
    forged = jwt.encode({"admin": False}, "test-admin-secret", algorithm="HS256")
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 403


def test_admin_login_endpoint_is_registered_on_real_app(client, monkeypatch):
    """실제 FastAPI 앱(main.py)에도 라우터가 등록돼 있는지 확인하는 통합
    테스트 — client fixture는 backend/tests/conftest.py가 제공한다."""
    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", "real-app-test-pw")
    res = client.post("/admin-auth/login", json={"password": "real-app-test-pw"})
    assert res.status_code == 200
    assert "access_token" in res.json()
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && python -m pytest tests/test_admin_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.admin_auth'`
(마지막 테스트는 `main.py`를 아직 안 고쳐서 라우터가 없어 404로 실패할 것)

- [ ] **Step 3: `admin_auth.py` 작성**

`backend/app/routers/admin_auth.py`:

```python
"""admin 전용 로그인 — 사장님 회원(users 테이블) 체계와 완전히 무관하다.
단일 비밀번호(ADMIN_PASSWORD)만 검증하고, 성공하면 admin 전용 시크릿
(ADMIN_JWT_SECRET)으로 서명한 JWT를 돌려준다. DB 조회가 전혀 없다."""

import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "dev-admin-password-change-in-production")
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "dev-admin-secret-change-in-production-min-32-bytes")
ADMIN_JWT_ALGORITHM = "HS256"
ADMIN_JWT_EXPIRE_HOURS = 24 * 7

router = APIRouter(tags=["admin-auth"])
_admin_bearer = HTTPBearer(auto_error=False)


class AdminLoginRequest(BaseModel):
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str


def create_admin_token() -> str:
    payload = {
        "admin": True,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ADMIN_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)


@router.post("/admin-auth/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginRequest):
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "비밀번호가 올바르지 않습니다")
    return AdminLoginResponse(access_token=create_admin_token())


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_admin_bearer),
) -> None:
    """admin 전용 라우트에서 쓰는 FastAPI dependency. ADMIN_JWT_SECRET으로만
    검증하고 DB 조회가 없다 — admin이 User 레코드가 아니기 때문이다."""
    if credentials is None:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        payload = jwt.decode(credentials.credentials, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "유효하지 않은 토큰입니다")
    if not payload.get("admin"):
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return None
```

- [ ] **Step 4: `main.py`에 라우터 등록 + CORS에 admin 오리진 추가**

`backend/app/main.py`에서 아래 세 곳을 수정한다.

라우터 import 줄(파일 상단)을 바꾼다:

```python
# 기존
from app.routers import admin, admin_llmops, ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections, style_principles
```

```python
# 변경
from app.routers import admin, admin_auth, admin_llmops, ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections, style_principles
```

CORS 설정 블록을 바꾼다:

```python
# 기존
_frontend_origin = os.getenv("FRONTEND_ORIGIN")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_origins=[_frontend_origin] if _frontend_origin else [],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

```python
# 변경
_frontend_origin = os.getenv("FRONTEND_ORIGIN")
_admin_frontend_origin = os.getenv("ADMIN_FRONTEND_ORIGIN")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_origins=[o for o in (_frontend_origin, _admin_frontend_origin) if o],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

라우터 등록 줄(파일 하단)을 바꾼다:

```python
# 기존
app.include_router(admin.router)
app.include_router(admin_llmops.router)
```

```python
# 변경
app.include_router(admin.router)
app.include_router(admin_auth.router)
app.include_router(admin_llmops.router)
```

- [ ] **Step 5: `.env.example`에 새 환경변수 설명 추가**

`backend/.env.example` 맨 끝에 추가:

```
# admin 전용 인증 — 사장님 회원(users 테이블)과 완전히 무관하다. 단일
# 비밀번호만 검증하고 ADMIN_JWT_SECRET으로 서명한 토큰을 돌려준다. 둘 다
# 미설정 시 개발용 기본값을 쓰지만 운영 배포에서는 반드시 채운다.
ADMIN_PASSWORD=
ADMIN_JWT_SECRET=

# 별도 배포된 admin 프론트엔드 도메인 (예: https://admin-xxx.up.railway.app).
# CORS 허용 오리진에 추가된다 — 비워두면 admin 앱이 이 백엔드를 호출할 때
# CORS 에러가 난다(로컬 개발은 localhost 정규식으로 자동 허용되므로 비워둬도 됨).
ADMIN_FRONTEND_ORIGIN=
```

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `cd backend && python -m pytest tests/test_admin_auth.py -v`
Expected: PASS (7개 테스트 전부)

- [ ] **Step 7: 커밋**

```bash
cd backend
git add app/routers/admin_auth.py app/main.py .env.example tests/test_admin_auth.py
git commit -m "feat: admin 전용 비밀번호 인증(admin-auth 로그인 + require_admin_token) 추가"
```

---

### Task 2: 백엔드 — 기존 admin 라우터를 require_admin_token으로 전환

**Files:**
- Modify: `backend/app/auth.py`
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/routers/admin_llmops.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_admin.py`
- Modify: `backend/tests/test_admin_llmops.py`

**Interfaces:**
- Consumes: Task 1의 `app.routers.admin_auth.require_admin_token`(파라미터
  없는 FastAPI dependency), `app.routers.admin_auth.router`(이미 `main.py`에
  등록돼 있어 `/admin-auth/login`을 호출할 수 있음).
- Produces: `backend/tests/conftest.py`의 새 `admin_headers` fixture(dict
  `{"Authorization": "Bearer <token>"}`, 이후 어떤 테스트에서도 재사용
  가능) — 이후 플랜 태스크에는 이 fixture를 쓰는 새 백엔드 테스트가 없지만,
  향후 admin 엔드포인트 테스트를 추가할 때 이 fixture를 그대로 쓴다.

- [ ] **Step 1: `conftest.py`에 `admin_headers` fixture 추가**

`backend/tests/conftest.py`의 기존 `auth_headers` fixture 바로 뒤에 추가한다.

```python
@pytest.fixture()
def admin_headers(client, monkeypatch):
    from app.routers import admin_auth

    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", "test-admin-pw")
    res = client.post("/admin-auth/login", json={"password": "test-admin-pw"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 2: `test_admin.py`를 새 인증 방식으로 다시 작성(실패 상태로)**

`backend/tests/test_admin.py` 전체를 아래 내용으로 교체한다 — 이 시점에는
`admin.py`가 아직 안 바뀌어서 `admin_headers`로 호출해도 (여전히
`require_admin`이 User.role을 보기 때문에) 403이 나와 일부 테스트가 실패해야
정상이다.

```python
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Payment, ReplySetting, ReviewSyncJob, Store, StorePlatformConnection, Subscription, User


def test_user_role_defaults_to_owner(db_session):
    user = User(nickname="테스트", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.role == "owner"


def test_auth_me_includes_role(client, seeded_user, auth_headers):
    res = client.get("/auth/me", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["role"] == "owner"


def test_admin_payments_requires_admin_auth(client, seeded_user, auth_headers):
    res = client.get("/admin/payments", headers=auth_headers)
    assert res.status_code == 401


def test_admin_payments_lists_recent_payments(client, db_session, seeded_user, admin_headers):
    db_session.add(Payment(
        user_id=seeded_user["user"].id, order_id="order-1", plan="pro", amount=19900,
        status="approved", requested_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.get("/admin/payments", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-1"
    assert body[0]["user_email"] == "demo@dris.kr"
    assert body[0]["status"] == "approved"


def test_admin_payments_filters_by_status(client, db_session, seeded_user, admin_headers):
    db_session.add_all([
        Payment(user_id=seeded_user["user"].id, order_id="order-a", plan="pro", amount=19900,
                status="approved", requested_at=datetime.now(timezone.utc)),
        Payment(user_id=seeded_user["user"].id, order_id="order-b", plan="pro", amount=19900,
                status="failed", requested_at=datetime.now(timezone.utc), fail_reason="카드 한도 초과"),
    ])
    db_session.commit()

    res = client.get("/admin/payments?status=failed", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["order_id"] == "order-b"
    assert body[0]["fail_reason"] == "카드 한도 초과"


def test_admin_stores_only_includes_baemin_connections_with_credentials(
    client, db_session, seeded_user, platforms, admin_headers,
):
    store = seeded_user["store"]

    # seeded_user가 이미 만들어둔 연결은 credential_ciphertext가 NULL이라 제외돼야 한다
    res = client.get("/admin/stores", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == []


def test_admin_stores_includes_latest_sync_job_and_auto_reply_state(
    client, db_session, seeded_user, platforms, reply_styles, admin_headers,
):
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

    res = client.get("/admin/stores", headers=admin_headers)
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
    client, db_session, seeded_user, platforms, admin_headers,
):
    store = seeded_user["store"]
    conn = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == store.id)
    )
    conn.credential_ciphertext = "encrypted-blob"
    db_session.commit()

    res = client.get("/admin/stores", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["last_sync"] is None
    assert body[0]["auto_reply_enabled"] is False  # reply_settings 행이 아예 없을 때의 기본값


def test_admin_toggle_auto_reply_updates_setting(
    client, db_session, seeded_user, reply_styles, admin_headers,
):
    store = seeded_user["store"]
    rs = ReplySetting(store_id=store.id, style_id=reply_styles.id, auto_reply_enabled=True, auto_reply_min_rating=5)
    db_session.add(rs)
    db_session.commit()

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": False}, headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is False

    db_session.refresh(rs)
    assert rs.auto_reply_enabled is False


def test_admin_toggle_auto_reply_404_when_no_settings(client, db_session, seeded_user, admin_headers):
    store = seeded_user["store"]

    res = client.patch(
        f"/admin/stores/{store.id}/auto-reply", json={"enabled": True}, headers=admin_headers,
    )
    assert res.status_code == 404


def test_admin_users_search_by_email_or_nickname(client, db_session, seeded_user, admin_headers):
    other = User(
        email="another@example.com", nickname="다른사장",
        password_hash="x", marketing_agreed=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.commit()

    res = client.get("/admin/users?q=demo", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["email"] == "demo@dris.kr"
    assert body[0]["plan"] == "basic"
    assert body[0]["store_count"] == 1


def test_admin_users_no_query_returns_recent_users(client, db_session, seeded_user, admin_headers):
    res = client.get("/admin/users", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()) >= 1


def test_admin_set_plan_to_pro_sets_expires_at_from_days(client, db_session, seeded_user, admin_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 14},
        headers=admin_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "pro"
    assert body["expires_at"] == str(date.today() + timedelta(days=14))


def test_admin_set_plan_to_pro_extends_existing_future_expiry(client, db_session, seeded_user, admin_headers):
    # 이미 Pro로 30일 남은 사용자에게 관리자가 "7일 더" 부여하면, 오늘부터 7일이
    # 아니라 기존 만료일(오늘+30일)부터 7일을 더 연장해서 오늘+37일이 돼야 한다.
    # billing.py의 _approve_payment(결제 승인 자동 연장)와 동일한 규칙이다 —
    # 그렇지 않으면 관리자의 지원성 플랜 부여가 오히려 구독을 단축시킨다.
    sub = db_session.scalar(select(Subscription).where(Subscription.user_id == seeded_user["user"].id))
    sub.plan = "pro"
    sub.expires_at = date.today() + timedelta(days=30)
    db_session.commit()

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 7},
        headers=admin_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "pro"
    assert body["expires_at"] == str(date.today() + timedelta(days=37))


def test_admin_set_plan_to_pro_defaults_to_30_days(client, db_session, seeded_user, admin_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro"}, headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["expires_at"] == str(date.today() + timedelta(days=30))


def test_admin_set_plan_to_basic_clears_expires_at(client, db_session, seeded_user, admin_headers):
    client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "pro", "days": 30}, headers=admin_headers,
    )

    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "basic"}, headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["plan"] == "basic"
    assert res.json()["expires_at"] is None


def test_admin_set_plan_rejects_out_of_range_days(client, db_session, seeded_user, admin_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro", "days": 400},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_admin_set_plan_404_for_unknown_user(client, db_session, seeded_user, admin_headers):
    res = client.patch("/admin/users/999999/plan", json={"plan": "pro"}, headers=admin_headers)
    assert res.status_code == 404


def test_admin_set_plan_rejects_invalid_plan_value(client, db_session, seeded_user, admin_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan", json={"plan": "enterprise"}, headers=admin_headers,
    )
    assert res.status_code == 422


@pytest.mark.parametrize("path", ["/admin/payments", "/admin/stores", "/admin/users"])
def test_admin_get_endpoints_reject_store_owner_token(client, seeded_user, auth_headers, path):
    res = client.get(path, headers=auth_headers)
    assert res.status_code == 401


def test_admin_toggle_auto_reply_rejects_store_owner_token(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/stores/{seeded_user['store'].id}/auto-reply",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert res.status_code == 401


def test_admin_set_plan_rejects_store_owner_token(client, seeded_user, auth_headers):
    res = client.patch(
        f"/admin/users/{seeded_user['user'].id}/plan",
        json={"plan": "pro"},
        headers=auth_headers,
    )
    assert res.status_code == 401
```

- [ ] **Step 3: `test_admin_llmops.py`도 새 인증 방식으로 교체(실패 상태로)**

`backend/tests/test_admin_llmops.py` 전체를 아래 내용으로 교체한다.

```python
from datetime import datetime, timezone

from app.models import DraftFeedbackScore


def test_list_runs_requires_admin_auth(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/runs", headers=auth_headers)
    assert res.status_code == 401


def test_list_runs_returns_shaped_rows(client, db_session, seeded_user, admin_headers, monkeypatch):
    import app.routers.admin_llmops as admin_llmops_mod

    monkeypatch.setattr(
        admin_llmops_mod.observability, "list_recent_runs",
        lambda limit: [{"trace_id": "t1", "category_label": "배달(지연/파손)", "passed_verification": True}],
    )

    res = client.get("/admin/llmops/runs?limit=5", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {"runs": [{"trace_id": "t1", "category_label": "배달(지연/파손)", "passed_verification": True}]}


def test_run_detail_requires_admin_auth(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/runs/some-trace-id", headers=auth_headers)
    assert res.status_code == 401


def test_run_detail_returns_404_when_not_found(client, db_session, seeded_user, admin_headers, monkeypatch):
    import app.routers.admin_llmops as admin_llmops_mod

    monkeypatch.setattr(admin_llmops_mod.observability, "get_run_detail", lambda trace_id: None)

    res = client.get("/admin/llmops/runs/nonexistent", headers=admin_headers)

    assert res.status_code == 404


def test_run_detail_returns_node_data(client, db_session, seeded_user, admin_headers, monkeypatch):
    import app.routers.admin_llmops as admin_llmops_mod

    detail = {"trace_id": "t1", "nodes": [{"name": "retrieve_memory", "outputs": {"style_rules": "규칙"}}]}
    monkeypatch.setattr(admin_llmops_mod.observability, "get_run_detail", lambda trace_id: detail)

    res = client.get("/admin/llmops/runs/t1", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == detail


def test_accuracy_requires_admin_auth(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/accuracy", headers=auth_headers)
    assert res.status_code == 401


def test_accuracy_aggregates_across_all_stores(client, db_session, seeded_user, admin_headers):
    sid = seeded_user["store"].id
    db_session.add_all([
        DraftFeedbackScore(
            store_id=sid, category="delivery", similarity_score=0.9, trace_id="t1",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=sid, category="delivery", similarity_score=0.7, trace_id="t2",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=sid, category="no_issue", similarity_score=0.95, trace_id="t3",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    res = client.get("/admin/llmops/accuracy", headers=admin_headers)

    assert res.status_code == 200
    by_category = {c["category"]: c for c in res.json()["categories"]}
    assert by_category["delivery"]["sample_count"] == 2
    assert by_category["delivery"]["avg_similarity"] == 0.8
    assert by_category["delivery"]["label"] == "배달(지연/파손)"
    assert by_category["no_issue"]["label"] == "특이 불만 없음"


def test_accuracy_empty_when_no_scores(client, db_session, seeded_user, admin_headers):
    res = client.get("/admin/llmops/accuracy", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {"categories": []}
```

- [ ] **Step 4: 테스트 실행해서 실패 확인**

Run: `cd backend && python -m pytest tests/test_admin.py tests/test_admin_llmops.py -v`
Expected: FAIL — `admin_headers`로 호출한 테스트들이 403(여전히 User.role
체크 중이라 admin 토큰을 이해 못 함)으로 실패.

- [ ] **Step 5: `admin.py`를 require_admin_token으로 전환**

`backend/app/routers/admin.py`에서 import 줄을 바꾼다:

```python
# 기존
from app.auth import require_admin
```

```python
# 변경
from app.routers.admin_auth import require_admin_token
```

그리고 파일 안의 `admin: User = Depends(require_admin)` 5곳을 전부
`_: None = Depends(require_admin_token)`로 바꾼다(정확히 같은 문자열이
5번 나오므로 "모두 바꾸기"로 한 번에 처리하면 된다).

- [ ] **Step 6: `admin_llmops.py`를 require_admin_token으로 전환**

`backend/app/routers/admin_llmops.py`에서 두 import 줄을 바꾼다:

```python
# 기존
from app.auth import require_admin
```

```python
# 변경
from app.routers.admin_auth import require_admin_token
```

```python
# 기존
from app.models import DraftFeedbackScore, User
```

```python
# 변경
from app.models import DraftFeedbackScore
```

(이 파일은 이제 `User`를 타입으로도 안 쓰므로 import에서 완전히 뺀다.)

그리고 파일 안의 `admin: User = Depends(require_admin)` 3곳을 전부
`_: None = Depends(require_admin_token)`로 바꾼다.

- [ ] **Step 7: `auth.py`에서 `require_admin` 삭제**

`backend/app/auth.py` 맨 끝의 `require_admin` 함수(마지막 함수) 전체를
삭제한다:

```python
# 삭제할 블록
def require_admin(user: User = Depends(get_current_user)) -> User:
    """관리자 전용 라우트에서 쓰는 FastAPI dependency. require_pro_plan과 같은 패턴 —
    프론트에서만 막으면 개발자도구로 백엔드를 직접 두드려 우회할 수 있으므로 백엔드에서도
    강제해야 한다."""
    if user.role != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user
```

- [ ] **Step 8: 테스트 실행해서 통과 확인**

Run: `cd backend && python -m pytest -q`
Expected: PASS — 전체 스위트(기존 736개 테스트 + Task 1의 7개 + 이번 태스크의
변경분)가 전부 통과해야 한다. `require_admin`을 지웠는데도 다른 곳에서
그 이름을 import하는 곳이 남아있으면 여기서 `ImportError`로 드러난다
(남아있으면 안 됨 — 이 플랜을 쓰기 전에 `grep -rn "require_admin\b" app
tests`로 `admin.py`/`admin_llmops.py`/`auth.py` 외에는 참조가 없음을 이미
확인했다).

- [ ] **Step 9: 커밋**

```bash
cd backend
git add app/auth.py app/routers/admin.py app/routers/admin_llmops.py tests/conftest.py tests/test_admin.py tests/test_admin_llmops.py
git commit -m "refactor: admin 라우터를 require_admin(User.role)에서 require_admin_token(admin 전용)으로 전환"
```

---

### Task 3: admin 앱 — 프로젝트 스캐폴딩 + 로그인 화면

**Files:**
- Create: `admin/package.json`
- Create: `admin/tsconfig.json`
- Create: `admin/next.config.ts`
- Create: `admin/postcss.config.mjs`
- Create: `admin/eslint.config.mjs`
- Create: `admin/.gitignore`
- Create: `admin/railway.json`
- Create: `admin/.env.example`
- Create: `admin/src/app/globals.css`
- Create: `admin/src/app/layout.tsx`
- Create: `admin/src/lib/api.ts`
- Create: `admin/src/components/Logo.tsx`
- Create: `admin/src/app/login/page.tsx`

**Interfaces:**
- Consumes: Task 1의 `POST /admin-auth/login`(백엔드, body
  `{password: string}`, 응답 `{access_token: string}`).
- Produces: `admin/src/lib/api.ts`의 `getToken`/`setToken`/`clearToken`
  (localStorage 키 `"dris_admin_token"`)과 `apiGet`/`apiPost`/`apiPatch`
  제네릭 함수, `ApiError` 클래스 — 이후 모든 admin 화면 태스크가 이 모듈을
  그대로 가져다 쓴다. `admin/src/components/Logo.tsx`의 `Logo` 컴포넌트 —
  Task 4의 `AdminNav`도 재사용한다.

- [ ] **Step 1: 프로젝트 설정 파일 작성**

`admin/package.json`:

```json
{
  "name": "admin",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint"
  },
  "dependencies": {
    "next": "16.2.11",
    "react": "19.2.4",
    "react-dom": "19.2.4"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4",
    "@types/node": "^20",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "eslint": "^9",
    "eslint-config-next": "16.2.11",
    "tailwindcss": "^4",
    "typescript": "^5"
  }
}
```

`admin/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "incremental": true,
    "plugins": [
      {
        "name": "next"
      }
    ],
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx",
    ".next/types/**/*.ts",
    ".next/dev/types/**/*.ts",
    "**/*.mts"
  ],
  "exclude": ["node_modules"]
}
```

`admin/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {};

export default nextConfig;
```

`admin/postcss.config.mjs`:

```js
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
```

`admin/eslint.config.mjs`:

```js
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
```

`admin/.gitignore`:

```
# dependencies
/node_modules
/.pnp
.pnp.*
.yarn/*
!.yarn/patches
!.yarn/plugins
!.yarn/releases
!.yarn/versions

# testing
/coverage

# next.js
/.next/
/out/

# production
/build

# misc
.DS_Store
*.pem

# debug
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*

# env files (can opt-in for committing if needed)
.env*
!.env.example

# vercel
.vercel

# typescript
*.tsbuildinfo
next-env.d.ts
```

`admin/railway.json`:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "RAILPACK",
    "buildCommand": "npm run build"
  },
  "deploy": {
    "startCommand": "npm start -- -p $PORT",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

`admin/.env.example`:

```
# 로컬 개발용 예시. 실제 값을 채운 .env.local은 커밋하지 않는다.

# 기존 backend 서비스 도메인 (운영: Railway backend 도메인, 로컬: http://localhost:8000)
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 2: 디자인 토큰 + 루트 레이아웃 작성**

`admin/src/app/globals.css`:

```css
@import "tailwindcss";

:root {
  --background: #0b0e14;
  --surface: #12161f;
  --surface-2: #171c27;
  --border-subtle: #232935;
  --foreground: #e7e9ee;
  --muted: #8b93a7;
  --accent: #6d5ef5;
  --accent-soft: #6d5ef526;
  --success: #34d399;
  --warning: #fbbf24;
  --danger: #f87171;
  --danger-soft: #f8717126;
}

@theme inline {
  --color-background: var(--background);
  --color-surface: var(--surface);
  --color-surface-2: var(--surface-2);
  --color-border-subtle: var(--border-subtle);
  --color-foreground: var(--foreground);
  --color-muted: var(--muted);
  --color-accent: var(--accent);
  --color-accent-soft: var(--accent-soft);
  --color-success: var(--success);
  --color-warning: var(--warning);
  --color-danger: var(--danger);
  --color-danger-soft: var(--danger-soft);
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}

body {
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-sans), -apple-system, "Malgun Gothic", sans-serif;
}

* {
  scrollbar-color: var(--border-subtle) transparent;
}
```

`admin/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "스토어 타겟 Admin",
  description: "관리자 전용 — 외부에 공개되지 않음",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">{children}</body>
    </html>
  );
}
```

- [ ] **Step 3: API 클라이언트 + Logo 작성**

`admin/src/lib/api.ts`:

```ts
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "dris_admin_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  errorCode?: string;
  constructor(status: number, message: string, errorCode?: string) {
    super(message);
    this.status = status;
    this.errorCode = errorCode;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : (detail?.message ?? `요청 실패 (${res.status})`);
    const errorCode = typeof detail === "object" && detail !== null ? detail.error_code : undefined;
    throw new ApiError(res.status, message, errorCode);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const apiGet = <T,>(path: string) => request<T>(path);
export const apiPost = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const apiPut = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });
export const apiPatch = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) });
export const apiDelete = <T,>(path: string) => request<T>(path, { method: "DELETE" });

export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
export const percent = (n: number) => `${(n * 100).toFixed(1)}%`;
```

`admin/src/components/Logo.tsx`:

```tsx
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="42" fill="none" stroke="#6D5EF5" strokeWidth="7" opacity="0.32" />
      <circle cx="50" cy="50" r="27" fill="none" stroke="#6D5EF5" strokeWidth="7" />
      <rect x="24" y="34" width="52" height="9" rx="2" fill="#F5F4FF" />
      <rect x="45.5" y="34" width="9" height="43" rx="2" fill="#F5F4FF" />
    </svg>
  );
}
```

- [ ] **Step 4: 로그인 화면 작성**

`admin/src/app/login/page.tsx`:

```tsx
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiPost, getToken, setToken } from "@/lib/api";
import { Logo } from "@/components/Logo";

type AdminLoginResponse = { access_token: string };

export default function AdminLoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (getToken()) router.replace("/");
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<AdminLoginResponse>("/admin-auth/login", { password });
      setToken(res.access_token);
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "로그인에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-8 flex items-center gap-2.5">
          <Logo size={36} />
          <div>
            <p className="text-base font-semibold">스토어 타겟</p>
            <p className="text-xs text-muted">Admin</p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-muted">비밀번호</label>
            <input
              type="password"
              required
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="••••••••"
            />
          </div>

          {error && <p className="text-xs text-danger">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "로그인 중..." : "로그인"}
          </button>
        </form>
      </div>
    </main>
  );
}
```

- [ ] **Step 5: 의존성 설치 + 타입체크 + 빌드 확인**

Run:
```bash
cd admin
npm install
npx tsc --noEmit
npm run build
```
Expected: 셋 다 에러 없이 끝난다(`npm run build`가 `/login` 라우트 하나만
있어도 성공해야 한다).

- [ ] **Step 6: 수동 확인**

Run: `cd admin && npm run dev`
브라우저로 `http://localhost:3000/login`(frontend가 이미 3000번을 쓰고
있다면 Next가 자동으로 3001 등 다른 포트를 골라 터미널에 출력한다)에
접속해서 비밀번호 입력 폼이 보이는지 확인한다. (백엔드가 로컬에서
`ADMIN_PASSWORD` 없이 떠 있다면 기본값 `dev-admin-password-change-in-
production`으로 로그인되는지까지 확인해도 좋다 — 필수는 아님, Task 4에서
`/`로 리다이렉트되는 화면이 아직 없어 로그인 자체의 성공 여부는 네트워크
탭의 200 응답으로 확인한다.)

- [ ] **Step 7: 커밋**

```bash
cd admin
git add -A
git commit -m "feat: admin 앱 스캐폴딩 + 비밀번호 로그인 화면"
```

(`package-lock.json`도 `npm install`이 만들어주며, `git add -A`로 같이
커밋된다 — frontend/backend도 lockfile을 커밋하는 기존 관례와 동일하다.)

---

### Task 4: admin 앱 — 보호된 레이아웃 + 내비게이션 + 결제 이력 화면

**Files:**
- Create: `admin/src/components/AdminNav.tsx`
- Create: `admin/src/app/(dashboard)/layout.tsx`
- Create: `admin/src/app/(dashboard)/page.tsx`
- Create: `admin/src/app/(dashboard)/payments/page.tsx`

**Interfaces:**
- Consumes: Task 3의 `@/lib/api`(`getToken`/`clearToken`/`apiGet`/`won`),
  `@/components/Logo`.
- Produces: `admin/src/components/AdminNav.tsx`의 `AdminNav` 컴포넌트 —
  Task 5/6에서 만드는 `(dashboard)/stores`, `/users`, `/llmops` 페이지도
  이미 이 레이아웃 아래에 있으므로 따로 손댈 필요 없다(`NAV` 배열만
  한 번에 다 적어두고 다음 태스크들이 그 경로에 페이지를 채운다).

- [ ] **Step 1: 내비게이션 작성**

`admin/src/components/AdminNav.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { clearToken } from "@/lib/api";

const NAV = [
  { href: "/payments", label: "결제 이력" },
  { href: "/stores", label: "매장 운영 현황" },
  { href: "/users", label: "유저 관리" },
  { href: "/llmops", label: "LLMOps" },
];

export function AdminNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center justify-between border-b border-border-subtle bg-surface px-4 md:hidden">
        <div className="flex items-center gap-2">
          <Logo size={26} />
          <p className="text-sm font-semibold leading-tight">관리자</p>
        </div>
        <button
          onClick={() => setOpen(true)}
          aria-label="메뉴 열기"
          className="-mr-2 rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-5 w-5">
            <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-64 shrink-0 flex-col overflow-y-auto border-r border-border-subtle bg-surface transition-transform duration-200 ease-out md:static md:z-auto md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between gap-2.5 px-5 py-5">
          <div className="flex items-center gap-2.5">
            <Logo size={32} />
            <div>
              <p className="text-sm font-semibold leading-tight">스토어 타겟</p>
              <p className="text-[11px] text-muted leading-tight">관리자</p>
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            aria-label="메뉴 닫기"
            className="rounded-lg p-1.5 text-muted transition hover:bg-surface-2 hover:text-foreground md:hidden"
          >
            ✕
          </button>
        </div>

        <nav className="flex-1 space-y-1 px-3 pb-4">
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className={`block rounded-lg px-3 py-2.5 text-sm transition ${
                  active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border-subtle px-4 py-4">
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
    </>
  );
}
```

- [ ] **Step 2: 보호된 레이아웃 + 홈 리다이렉트 작성**

`admin/src/app/(dashboard)/layout.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AdminNav } from "@/components/AdminNav";
import { getToken } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
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
      <AdminNav />
      <main className="flex-1 overflow-y-auto px-4 pb-8 pt-20 md:px-8 md:py-8">{children}</main>
    </div>
  );
}
```

`admin/src/app/(dashboard)/page.tsx`:

```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function AdminHome() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/payments");
  }, [router]);

  return null;
}
```

- [ ] **Step 3: 결제 이력 화면 작성**

`admin/src/app/(dashboard)/payments/page.tsx`:

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
        <>
          <div className="space-y-3 md:hidden">
            {rows.map((r) => (
              <div key={r.order_id} className="rounded-2xl border border-border-subtle bg-surface p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium">{r.user_nickname}</p>
                    <p className="text-xs text-muted">{r.user_email ?? "카카오 계정"}</p>
                  </div>
                  <span className={`text-xs font-medium ${STATUS_LABEL[r.status].className}`}>
                    {STATUS_LABEL[r.status].label}
                  </span>
                </div>
                <dl className="mt-3 space-y-1 text-xs text-muted">
                  <div className="flex justify-between"><dt>플랜</dt><dd className="text-foreground">{r.plan}</dd></div>
                  <div className="flex justify-between"><dt>금액</dt><dd className="text-foreground">{won(r.amount)}</dd></div>
                  <div className="flex justify-between"><dt>요청 시각</dt><dd>{new Date(r.requested_at).toLocaleString("ko-KR")}</dd></div>
                  {r.fail_reason && <div className="flex justify-between"><dt>실패 사유</dt><dd>{r.fail_reason}</dd></div>}
                </dl>
              </div>
            ))}
          </div>

          <div className="hidden overflow-x-auto rounded-2xl border border-border-subtle bg-surface md:block">
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
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 타입체크 + 빌드 확인**

Run:
```bash
cd admin
npx tsc --noEmit
npm run build
```
Expected: 둘 다 에러 없이 끝난다.

- [ ] **Step 5: 수동 확인**

로컬 backend(`ADMIN_PASSWORD` 기본값 사용 가능)와 admin 앱을 모두
`npm run dev`/`uvicorn`으로 띄운 뒤, `/login`에서 비밀번호로 로그인 →
`/`로 리다이렉트 → 자동으로 `/payments`로 다시 리다이렉트 → 결제 이력
데이터(또는 빈 상태 문구)가 뜨는지 확인한다. 로그아웃 버튼을 누르면
`/login`으로 돌아가는지도 확인한다.

- [ ] **Step 6: 커밋**

```bash
cd admin
git add -A
git commit -m "feat: admin 앱 보호된 레이아웃 + 내비게이션 + 결제 이력 화면"
```

---

### Task 5: admin 앱 — 매장 운영 현황 + 유저 관리 화면

**Files:**
- Create: `admin/src/components/Toggle.tsx`
- Create: `admin/src/app/(dashboard)/stores/page.tsx`
- Create: `admin/src/app/(dashboard)/users/page.tsx`

**Interfaces:**
- Consumes: Task 3의 `@/lib/api`(`apiGet`/`apiPatch`), Task 4의
  `(dashboard)/layout.tsx`(이미 이 경로들을 감싸고 있음).
- Produces: 없음(이 태스크로 화면 포팅이 끝나는 마지막 두 화면 중 하나,
  Task 6은 독립적으로 llmops만 추가).

- [ ] **Step 1: Toggle 컴포넌트 작성**

`admin/src/components/Toggle.tsx`:

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

- [ ] **Step 2: 매장 운영 현황 화면 작성**

`admin/src/app/(dashboard)/stores/page.tsx`:

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

- [ ] **Step 3: 유저 관리 화면 작성**

`admin/src/app/(dashboard)/users/page.tsx`:

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
                  onClick={() => {
                    const days = daysInputs[r.user_id] || "30";
                    if (!confirm(`${r.nickname}님에게 Pro ${days}일을 부여할까요? (기존 만료일이 남아있으면 그 날짜부터 연장됩니다)`)) return;
                    changePlan(r.user_id, "pro");
                  }}
                  disabled={savingId === r.user_id}
                  className="rounded-lg border border-accent px-3 py-1.5 text-xs text-accent transition hover:bg-accent-soft disabled:opacity-50"
                >
                  Pro로 변경
                </button>
                <button
                  onClick={() => {
                    if (!confirm(`${r.nickname}님을 Basic으로 변경할까요? 구독이 즉시 만료됩니다.`)) return;
                    changePlan(r.user_id, "basic");
                  }}
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

- [ ] **Step 4: 타입체크 + 빌드 확인**

Run:
```bash
cd admin
npx tsc --noEmit
npm run build
```
Expected: 둘 다 에러 없이 끝난다.

- [ ] **Step 5: 커밋**

```bash
cd admin
git add -A
git commit -m "feat: admin 앱 매장 운영 현황 + 유저 관리 화면"
```

---

### Task 6: admin 앱 — LLMOps 대시보드 화면

**Files:**
- Create: `admin/src/app/(dashboard)/llmops/page.tsx`

**Interfaces:**
- Consumes: Task 3의 `@/lib/api`(`apiGet`), Task 4의 `(dashboard)/layout.tsx`.
- Produces: 없음(admin 앱의 화면 포팅은 이 태스크로 끝).

- [ ] **Step 1: LLMOps 화면 작성**

`admin/src/app/(dashboard)/llmops/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

type RunRow = {
  trace_id: string;
  started_at: string | null;
  status: string;
  category_label: string | null;
  passed_verification: boolean | null;
  retry_count: number | null;
  final_content_preview: string;
};

type RunNode = {
  name: string;
  run_type: string;
  status: string;
  start_time: string | null;
  latency_ms: number | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
};

type RunDetail = { trace_id: string; nodes: RunNode[] };

type AccuracyCategory = { category: string; label: string; avg_similarity: number; sample_count: number };

const NODE_NAME_LABEL: Record<string, string> = {
  retrieve_memory: "기억 조회",
  generate_draft: "초안 생성",
  ChatAnthropic: "Claude Sonnet 호출",
  verify_draft: "검증",
  route_after_verify: "분기 판단",
  fix_draft: "수정(재시도)",
  finalize: "최종화",
};

const PIPELINE_NODES: { key: string; title: string; summary: string; touches: string }[] = [
  {
    key: "retrieve_memory",
    title: "① 기억 조회",
    summary: "리뷰 카테고리에 맞는 절차/의미/일화 기억을 전부 모은다.",
    touches: "procedural_rules · brand_menu_info · brand_ceo_notices · golden_examples(3단계 검색) · store_style_profile(카테고리별 원칙)",
  },
  {
    key: "generate_draft",
    title: "② 초안 생성",
    summary: "모은 기억을 시스템 프롬프트에 꽂아 Claude Sonnet을 호출한다.",
    touches: "ChatAnthropic(langchain-anthropic) — 시스템: 원칙+예시+메뉴정보 / 유저: 리뷰 본문",
  },
  {
    key: "verify_draft",
    title: "③ 검증",
    summary: "LLM 재판단 없이 결정론적 체크만 한다 — 말투가 무난하게 수렴하는 걸 막기 위해서다.",
    touches: "이모지 정규식 체크 · few-shot 예시와의 문자열 유사도(복붙 체크)",
  },
  {
    key: "finalize",
    title: "④ 최종화",
    summary: "검증을 통과했거나 재시도 상한(2회)에 닿으면 끝낸다.",
    touches: "최종 답글 텍스트 + 통과 여부(passed_verification) 반환",
  },
];

function StatusBadge({ passed }: { passed: boolean | null }) {
  if (passed === null) return <span className="rounded bg-surface-2 px-2 py-0.5 text-[11px] text-muted">알 수 없음</span>;
  return passed ? (
    <span className="rounded bg-success/15 px-2 py-0.5 text-[11px] font-medium text-success">통과</span>
  ) : (
    <span className="rounded bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning">보류</span>
  );
}

function NodeIO({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return (
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</p>
        <p className="text-xs text-muted">없음</p>
      </div>
    );
  }
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</p>
      <div className="space-y-1.5">
        {entries.map(([key, value]) => (
          <div key={key} className="rounded-lg bg-surface px-2.5 py-1.5">
            <p className="text-[11px] text-accent">{key}</p>
            <p className="whitespace-pre-wrap break-words text-xs text-foreground">
              {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function RunDetailPanel({ trace_id }: { trace_id: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    apiGet<RunDetail>(`/admin/llmops/runs/${trace_id}`)
      .then(setDetail)
      .catch(() => setError("LangSmith에서 이 트레이스를 찾을 수 없어요(보관 기간이 지났거나 설정 문제일 수 있어요)."));
  }, [trace_id]);

  if (error) return <p className="px-4 py-3 text-xs text-danger">{error}</p>;
  if (!detail) return <p className="px-4 py-3 text-xs text-muted">노드별 상세 불러오는 중...</p>;

  return (
    <div className="space-y-3 border-t border-border-subtle bg-surface/60 p-4">
      {detail.nodes.map((node, i) => (
        <div key={i} className="rounded-xl border border-border-subtle bg-surface-2 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-foreground">
              {NODE_NAME_LABEL[node.name] ?? node.name}
              <span className="ml-2 text-xs text-muted">({node.name})</span>
            </p>
            <p className="text-[11px] text-muted">{node.latency_ms != null ? `${node.latency_ms}ms` : ""}</p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NodeIO title="입력" data={node.inputs} />
            <NodeIO title="출력" data={node.outputs} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AdminLlmopsPage() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyCategory[] | null>(null);
  const [openTraceId, setOpenTraceId] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ runs: RunRow[] }>("/admin/llmops/runs?limit=20").then((r) => setRuns(r.runs));
    apiGet<{ categories: AccuracyCategory[] }>("/admin/llmops/accuracy").then((r) => setAccuracy(r.categories));
  }, []);

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-lg font-semibold">LLMOps — 답글 생성 파이프라인</h1>
        <p className="text-xs text-muted">
          리뷰 답글이 실제로 어떤 노드를 거쳐, 어떤 데이터를 참조해서 만들어지는지 추적합니다.
        </p>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-4 text-sm font-semibold text-foreground">노드 구성도</h2>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
          {PIPELINE_NODES.map((node, i) => (
            <div key={node.key} className="flex flex-1 items-stretch gap-3">
              <div className="flex-1 rounded-xl border border-border-subtle bg-surface-2 p-3">
                <p className="text-sm font-semibold text-accent">{node.title}</p>
                <p className="mt-1 text-xs text-foreground">{node.summary}</p>
                <p className="mt-2 text-[11px] text-muted">{node.touches}</p>
              </div>
              {i < PIPELINE_NODES.length - 1 && (
                <div className="hidden items-center text-muted sm:flex">→</div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-3 rounded-xl border border-dashed border-border-subtle bg-surface-2/60 p-3">
          <p className="text-xs text-foreground">
            <span className="font-semibold text-warning">⑤ 수정(재시도)</span> — ③검증에서 위반이 발견되면(이모지는
            코드로 즉시 제거, 복붙은 겹친 예시를 빼고 좁게 재지시) 여기서 고친 뒤 ③검증으로 다시 돌아갑니다.
            최대 2회까지 반복하고, 그래도 안 풀리면 보류 상태로 ④최종화합니다.
          </p>
        </div>
        <p className="mt-3 text-[11px] text-muted">
          전체 실행은 LangSmith로 트레이싱되고, 재시도 루프가 끝나면 AI 초안과 사장님 최종본의 유사도가
          측정돼 아래 정확도 지표에 반영됩니다.
        </p>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-4 text-sm font-semibold text-foreground">카테고리별 정확도 (AI초안 ↔ 사장님최종본 유사도)</h2>
        {accuracy === null ? (
          <p className="text-sm text-muted">불러오는 중...</p>
        ) : accuracy.length === 0 ? (
          <p className="text-sm text-muted">아직 측정된 데이터가 없습니다.</p>
        ) : (
          <div className="space-y-3">
            {accuracy.map((c) => (
              <div key={c.category}>
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
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-1 text-sm font-semibold text-foreground">최근 실행 이력</h2>
        <p className="mb-4 text-xs text-muted">눌러서 열면 그 실행이 각 노드에서 실제로 주고받은 데이터를 볼 수 있어요.</p>
        {runs === null ? (
          <p className="text-sm text-muted">불러오는 중...</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-muted">
            표시할 실행 이력이 없어요 — LangSmith가 설정 안 됐거나(LANGSMITH_API_KEY), 아직 답글 생성이 없었을 수 있어요.
          </p>
        ) : (
          <div className="space-y-2">
            {runs.map((r) => (
              <div key={r.trace_id} className="overflow-hidden rounded-xl border border-border-subtle">
                <button
                  onClick={() => setOpenTraceId(openTraceId === r.trace_id ? null : r.trace_id)}
                  className="flex w-full items-center justify-between gap-3 bg-surface-2 px-4 py-3 text-left"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <StatusBadge passed={r.passed_verification} />
                    <span className="shrink-0 text-xs text-foreground">{r.category_label ?? "—"}</span>
                    <span className="truncate text-xs text-muted">{r.final_content_preview}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-[11px] text-muted">
                    {r.retry_count != null && r.retry_count > 0 && <span>재시도 {r.retry_count}회</span>}
                    <span>{r.started_at ? new Date(r.started_at).toLocaleString("ko-KR") : ""}</span>
                    <span>{openTraceId === r.trace_id ? "접기" : "자세히"}</span>
                  </div>
                </button>
                {openTraceId === r.trace_id && <RunDetailPanel trace_id={r.trace_id} />}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 타입체크 + 빌드 확인**

Run:
```bash
cd admin
npx tsc --noEmit
npm run build
```
Expected: 둘 다 에러 없이 끝난다.

- [ ] **Step 3: 커밋**

```bash
cd admin
git add -A
git commit -m "feat: admin 앱 LLMOps 대시보드 화면"
```

---

### Task 7: 정리 — 기존 /ops-4k9x2m 삭제 + 로그인 리다이렉트 수정 + 최종 검증

**Files:**
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/layout.tsx`
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/page.tsx`
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/payments/page.tsx`
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/stores/page.tsx`
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/users/page.tsx`
- Delete: `frontend/src/app/(admin)/ops-4k9x2m/llmops/page.tsx`
- Delete: `frontend/src/components/AdminSidebar.tsx`
- Modify: `frontend/src/app/login/page.tsx`

**Interfaces:**
- Consumes: Task 1~6이 전부 끝난 상태(admin 앱이 모든 화면을 자체적으로
  서빙하고 있어야 기존 경로를 지워도 기능 손실이 없다).
- Produces: 없음(이 플랜의 마지막 태스크).

- [ ] **Step 1: 기존 admin 라우트 그룹 삭제**

```bash
cd frontend
rm -rf "src/app/(admin)"
rm src/components/AdminSidebar.tsx
```

- [ ] **Step 2: 로그인 리다이렉트를 role 무관하게 단순화**

`frontend/src/app/login/page.tsx`에서 두 줄을 바꾼다.

```tsx
// 기존
      .then((me) => router.replace(me.role === "admin" ? "/ops-4k9x2m" : "/dashboard"))
```

```tsx
// 변경
      .then(() => router.replace("/dashboard"))
```

```tsx
// 기존
      router.push(res.user.role === "admin" ? "/ops-4k9x2m" : "/dashboard");
```

```tsx
// 변경
      router.push("/dashboard");
```

- [ ] **Step 3: 남은 참조가 없는지 확인**

Run:
```bash
cd frontend
grep -rn "ops-4k9x2m" src || echo "no matches"
grep -rn "AdminSidebar" src || echo "no matches"
```
Expected: 둘 다 "no matches" (grep이 아무것도 못 찾으면 종료 코드 1이라
`||`로 받아준다).

- [ ] **Step 4: 프론트엔드 타입체크 + 빌드**

Run:
```bash
cd frontend
npx tsc --noEmit
npm run build
```
Expected: 둘 다 에러 없이 끝난다 — 삭제된 파일을 참조하는 곳이 남아있으면
여기서 드러난다.

- [ ] **Step 5: 백엔드 전체 테스트 스위트 재확인**

Run: `cd backend && python -m pytest -q`
Expected: PASS(Task 2에서 이미 확인했지만, 이 태스크에서 백엔드 코드를
건드리지 않았는지 최종 확인하는 차원에서 다시 돌린다).

- [ ] **Step 6: admin 앱 타입체크 + 빌드 재확인**

Run:
```bash
cd admin
npx tsc --noEmit
npm run build
```
Expected: 둘 다 에러 없이 끝난다.

- [ ] **Step 7: 커밋**

```bash
cd frontend
git add -A
git commit -m "refactor: 사장님 프론트엔드에서 관리자 패널(/ops-4k9x2m) 완전 제거 — admin 앱으로 분리 완료"
```

---

## 플랜 완료 후 (코디네이터가 직접 수행, 태스크 아님)

모든 태스크가 리뷰를 통과하면, 설계 문서(`docs/superpowers/specs/
2026-10-07-admin-panel-separation-design.md`)의 "배포 순서" 절을 그대로
따라 Railway에 반영한다:

1. 새 Railway 서비스 `admin` 생성, `admin/` 디렉터리로 최초 배포(도메인
   확보 목적).
2. 받은 admin 도메인을 backend 서비스의 `ADMIN_FRONTEND_ORIGIN`에 설정.
3. backend에 `ADMIN_PASSWORD`/`ADMIN_JWT_SECRET` 설정(둘 다 실제 운영
   값으로, 개발용 기본값을 그대로 쓰지 않는다).
4. admin 서비스에 `NEXT_PUBLIC_API_URL`(기존 backend 도메인)을 설정.
5. backend 재배포 → admin 재배포.
6. admin 앱에서 실제 로그인 + 4개 화면 데이터 확인.
7. frontend 재배포(Task 7에서 삭제한 `/ops-4k9x2m`이 운영에도 반영되도록).
