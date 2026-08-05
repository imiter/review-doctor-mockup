# 카카오 소셜 로그인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 이메일 로그인을 유지한 채 "카카오로 로그인" 버튼으로 실제 OAuth 로그인/가입이 되게 한다.

**Architecture:** 프론트가 카카오 인가 URL로 리다이렉트 → 카카오가 프론트 콜백 페이지로 `code`를 돌려줌 → 프론트가 그 `code`를 백엔드 `POST /auth/kakao/callback`에 전달 → 백엔드가 카카오 토큰 교환 + 사용자 조회 후 기존 JWT 발급 로직을 재사용. 신규 `social_accounts` 테이블로 카카오 계정과 `users`를 연결한다.

**Tech Stack:** FastAPI, SQLAlchemy, httpx(이미 의존성에 있음), PostgreSQL(schema.sql), Next.js App Router(`useSearchParams` + `Suspense`).

## Global Constraints

- 카카오 비즈니스 미인증 상태라 이메일 동의 항목을 받지 못한다 — 카카오 고유 회원번호 + 닉네임만으로 로그인/가입한다.
- 이메일 회원가입(`/auth/signup`, `/auth/login`) 기존 동작은 그대로 유지한다.
- 신규 카카오 가입자도 기존 이메일 가입과 동일하게 기본 매장 1개 + 배민 연결 + Basic 구독을 자동 생성한다.
- 카카오 REST API 키/시크릿은 백엔드 환경변수에만 두고 프론트에 노출하지 않는다. (단, `NEXT_PUBLIC_KAKAO_CLIENT_ID`는 원래 브라우저 URL에 노출되는 공개 client_id라 예외.)
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 `DROP TABLE ... CASCADE` 후 전체 재생성하는 방식의 DB 정본이다. 증분 마이그레이션(`ALTER TABLE`)이 아니라 `CREATE TABLE` 정의 자체를 바로 고친다.
- 카카오 REST API 키(테스트용): `013b6d77c13fe0a1eb20e41d1bc012d4`. 레포에 커밋하지 않는다.

---

### Task 1: DB 스키마 — `social_accounts` 테이블 + `users` nullable 컬럼

**Files:**
- Modify: `schema.sql:4` (테이블 개수 주석), `schema.sql:21-26` (DROP TABLE 목록), `schema.sql:31-39` (users 테이블), `schema.sql:276-278` (alerts 테이블과 COMMIT 사이에 social_accounts 추가)
- Modify: `backend/app/models.py:10` (import), `backend/app/models.py:16-28` (User 클래스)
- Modify: `CLAUDE.md` (DB 설계 절 "16개 테이블" → "17개 테이블", 테이블 목록/용도/관계에 social_accounts 추가)
- Modify: `README.md:25-29` (테이블 개수/목록)
- Test: `backend/tests/test_auth.py` (파일 끝에 추가)

**Interfaces:**
- Produces: SQLAlchemy 모델 `SocialAccount(id, user_id, provider, provider_user_id, connected_at)`, `User.email: str | None`, `User.password_hash: str | None` — Task 3에서 그대로 가져다 쓴다.

- [ ] **Step 1: `schema.sql` 헤더 주석의 테이블 개수 갱신**

`schema.sql:4`를 다음으로 교체:

```sql
-- 17개 테이블. 모든 FK에 ON DELETE 정책 명시.
```

- [ ] **Step 2: DROP TABLE 목록에 `social_accounts` 추가**

`schema.sql:21-26`을 다음으로 교체:

```sql
DROP TABLE IF EXISTS
    social_accounts, alerts, ad_rank_snapshots, ad_performance_metrics, ad_campaigns,
    repurchase_metrics, daily_settlements, review_replies, reviews, orders,
    reply_settings, reply_styles, subscriptions, store_platform_connections,
    platforms, stores, users
CASCADE;
```

- [ ] **Step 3: `users` 테이블의 `email`, `password_hash`를 nullable로 변경**

`schema.sql:31-39`을 다음으로 교체:

```sql
CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    email            VARCHAR(255) UNIQUE,           -- 카카오 전용 계정은 이메일 없을 수 있음 (비즈니스 미인증)
    password_hash    VARCHAR(255),                  -- bcrypt. 카카오 전용 계정은 NULL (이메일 로그인용, 합의 사항)
    nickname         VARCHAR(50)  NOT NULL,
    phone_hash       CHAR(64),                     -- SHA-256 hex. 전화번호 원문 저장 금지
    marketing_agreed BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

(일반 `UNIQUE` 제약은 표준 SQL에서 NULL끼리는 서로 다른 값으로 취급되어 여러 개의 NULL 이메일이 허용된다 — 별도의 partial unique index가 필요 없다.)

- [ ] **Step 4: `social_accounts` 테이블을 `alerts` 다음, `COMMIT` 앞에 추가**

`schema.sql:276`(`CREATE INDEX idx_alerts_store_unread ...` 다음 줄)과 `schema.sql:278`(`COMMIT;`) 사이에 삽입:

```sql

-- ----------------------------------------------------------------------------
-- 17. social_accounts — 소셜 로그인 연결(카카오 등). users 1:N social_accounts
--     provider 문자열 기반이라 이후 provider(네이버/구글 등)가 늘어도 스키마
--     재변경 없이 행만 추가하면 된다.
-- ----------------------------------------------------------------------------
CREATE TABLE social_accounts (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider          VARCHAR(20) NOT NULL,          -- kakao (향후 naver, google 등)
    provider_user_id  VARCHAR(100) NOT NULL,         -- 카카오 고유 회원번호
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_social_accounts_user ON social_accounts(user_id);
```

- [ ] **Step 5: `models.py`에 `SocialAccount` 추가 + `User` nullable 반영**

`backend/app/models.py:10`을 다음으로 교체:

```python
from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
```

`backend/app/models.py:16-28`(`class User` 전체)를 다음으로 교체:

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    marketing_agreed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]

    stores: Mapped[list["Store"]] = relationship(back_populates="user")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="user")
    social_accounts: Mapped[list["SocialAccount"]] = relationship(back_populates="user")


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_user_id: Mapped[str] = mapped_column(String(100))
    connected_at: Mapped[datetime]

    user: Mapped[User] = relationship(back_populates="social_accounts")
```

- [ ] **Step 6: 문서의 테이블 개수/목록 갱신**

`CLAUDE.md`의 `## DB 설계 (16개 테이블)` 절:
- 제목을 `## DB 설계 (17개 테이블)`로.
- 테이블 나열 줄 끝에 `social_accounts` 추가.
- "### 테이블 용도" 목록에 아래 줄 추가: `- social_accounts: 소셜 로그인(카카오 등) 연결. provider 문자열 기반이라 확장 대비.`
- "### 핵심 관계" 목록에 아래 줄 추가: `- users 1:N social_accounts`

`README.md:25-29`의 테이블 목록 줄에 `social_accounts`를 추가하고, 있다면 "16개 테이블" 표기를 "17개 테이블"로 바꾼다.

- [ ] **Step 7: 모델 테스트 작성 (nullable + unique 제약 확인)**

`backend/tests/test_auth.py` 파일 끝에 추가:

```python
def test_social_account_links_user_and_enforces_unique_provider_pair(db_session):
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import SocialAccount, User

    user = User(
        email=None, password_hash=None, nickname="카카오전용",
        marketing_agreed=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(SocialAccount(
        user_id=user.id, provider="kakao", provider_user_id="9999",
        connected_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    found = db_session.query(SocialAccount).filter_by(provider="kakao", provider_user_id="9999").one()
    assert found.user_id == user.id

    db_session.add(SocialAccount(
        user_id=user.id, provider="kakao", provider_user_id="9999",
        connected_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 8: 테스트 실행**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v`
Expected: 새 테스트 포함 전체 PASS (기존 `test_signup_...` 등도 email/password_hash가 nullable로 바뀐 것과 무관하게 그대로 통과해야 한다).

- [ ] **Step 9: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_auth.py CLAUDE.md README.md
git commit -m "feat: social_accounts 테이블 추가 + users 이메일/비밀번호 nullable로 변경"
```

---

### Task 2: 카카오 API 클라이언트 (`backend/app/kakao.py`)

**Files:**
- Create: `backend/app/kakao.py`
- Test: `backend/tests/test_kakao.py`

**Interfaces:**
- Consumes: 없음 (외부 `httpx`만 사용).
- Produces: `exchange_code_for_token(code: str, redirect_uri: str) -> str`, `fetch_kakao_user(access_token: str) -> KakaoUser`, `KakaoUser(id: str, nickname: str, email: str | None)`, `KakaoAuthError(Exception)` — Task 3에서 그대로 import해 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_kakao.py` 신규 생성:

```python
import pytest

from app import kakao


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_exchange_code_for_token_returns_access_token(monkeypatch):
    monkeypatch.setattr(kakao, "KAKAO_CLIENT_ID", "test-client-id")

    def fake_post(url, data, timeout):
        assert url == kakao._TOKEN_URL
        assert data["code"] == "auth-code-123"
        assert data["redirect_uri"] == "http://localhost:3000/auth/kakao/callback"
        assert data["client_id"] == "test-client-id"
        return _FakeResponse(200, {"access_token": "kakao-access-token"})

    monkeypatch.setattr(kakao.httpx, "post", fake_post)

    token = kakao.exchange_code_for_token("auth-code-123", "http://localhost:3000/auth/kakao/callback")
    assert token == "kakao-access-token"


def test_exchange_code_for_token_raises_on_failure(monkeypatch):
    monkeypatch.setattr(kakao.httpx, "post", lambda url, data, timeout: _FakeResponse(400, {"error": "invalid_grant"}))

    with pytest.raises(kakao.KakaoAuthError):
        kakao.exchange_code_for_token("bad-code", "http://localhost:3000/auth/kakao/callback")


def test_fetch_kakao_user_parses_nickname_and_email(monkeypatch):
    payload = {
        "id": 123456789,
        "kakao_account": {
            "is_email_valid": True,
            "email": "user@kakao.com",
            "profile": {"nickname": "김사장"},
        },
    }
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(200, payload))

    user = kakao.fetch_kakao_user("kakao-access-token")
    assert user.id == "123456789"
    assert user.nickname == "김사장"
    assert user.email == "user@kakao.com"


def test_fetch_kakao_user_without_email_consent_returns_none_email(monkeypatch):
    payload = {"id": 999, "kakao_account": {"profile": {"nickname": "미인증사장"}}}
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(200, payload))

    user = kakao.fetch_kakao_user("kakao-access-token")
    assert user.id == "999"
    assert user.nickname == "미인증사장"
    assert user.email is None


def test_fetch_kakao_user_raises_on_failure(monkeypatch):
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(401, {}))

    with pytest.raises(kakao.KakaoAuthError):
        kakao.fetch_kakao_user("expired-token")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_kakao.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kakao'`

- [ ] **Step 3: `backend/app/kakao.py` 구현**

```python
"""카카오 로그인 — 인가 코드를 access_token으로 교환하고 사용자 정보를 조회한다."""

import os
from dataclasses import dataclass

import httpx

KAKAO_CLIENT_ID = os.getenv("KAKAO_CLIENT_ID", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")  # 콘솔에서 활성화한 경우에만 사용

_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
_USER_URL = "https://kapi.kakao.com/v2/user/me"


class KakaoAuthError(Exception):
    pass


@dataclass
class KakaoUser:
    id: str
    nickname: str
    email: str | None


def exchange_code_for_token(code: str, redirect_uri: str) -> str:
    data = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET

    resp = httpx.post(_TOKEN_URL, data=data, timeout=10.0)
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 토큰 교환 실패: {resp.status_code}")
    return resp.json()["access_token"]


def fetch_kakao_user(access_token: str) -> KakaoUser:
    resp = httpx.get(_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0)
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 사용자 조회 실패: {resp.status_code}")

    body = resp.json()
    account = body.get("kakao_account", {})
    profile = account.get("profile", {})
    nickname = profile.get("nickname") or body.get("properties", {}).get("nickname") or "카카오사용자"
    email = account.get("email") if account.get("is_email_valid", True) else None
    return KakaoUser(id=str(body["id"]), nickname=nickname, email=email)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_kakao.py -v`
Expected: 5개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/kakao.py backend/tests/test_kakao.py
git commit -m "feat: 카카오 토큰 교환/사용자 조회 클라이언트 추가"
```

---

### Task 3: 백엔드 `/auth/kakao/callback` 엔드포인트 + 계정 매칭

**Files:**
- Modify: `backend/app/routers/auth.py` (전체 재작성)
- Test: `backend/tests/test_auth.py` (파일 끝에 추가)

**Interfaces:**
- Consumes: Task 2의 `exchange_code_for_token`, `fetch_kakao_user`, `KakaoUser`, `KakaoAuthError`. Task 1의 `SocialAccount` 모델.
- Produces: `POST /auth/kakao/callback` — 요청 `{code: str, redirect_uri: str}`, 응답은 기존 `TokenResponse`(`access_token`, `token_type`, `user`)와 동일 형태.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_auth.py` 파일 끝(Task 1에서 추가한 테스트 다음)에 추가:

```python
def test_kakao_login_creates_new_user_with_store_and_subscription(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="1001", nickname="카카오사장", email=None),
    )

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["nickname"] == "카카오사장"
    assert body["user"]["email"] is None

    stores = client.get("/stores", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert len(stores) == 1


def test_kakao_login_reuses_existing_social_account(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="2002", nickname="재로그인사장", email=None),
    )
    body = {"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"}

    first = client.post("/auth/kakao/callback", json=body).json()
    second = client.post("/auth/kakao/callback", json=body).json()

    assert first["user"]["id"] == second["user"]["id"]
    stores = client.get("/stores", headers={"Authorization": f"Bearer {second['access_token']}"}).json()
    assert len(stores) == 1  # 두 번째 로그인에서 매장이 또 생기면 안 됨


def test_kakao_login_links_to_existing_email_account(client, platforms, seeded_user, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="3003", nickname="김사장", email="demo@dris.kr"),
    )

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 200
    assert res.json()["user"]["id"] == seeded_user["user"].id

    stores = client.get("/stores", headers={"Authorization": f"Bearer {res.json()['access_token']}"}).json()
    assert len(stores) == 1  # 기존 계정에 연결됐을 뿐, 새 매장이 추가로 생기면 안 됨


def test_kakao_login_failure_returns_502(client, platforms, monkeypatch):
    from app.kakao import KakaoAuthError
    from app.routers import auth as auth_router

    def _raise(*args, **kwargs):
        raise KakaoAuthError("boom")

    monkeypatch.setattr(auth_router, "exchange_code_for_token", _raise)

    res = client.post(
        "/auth/kakao/callback",
        json={"code": "bad-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )
    assert res.status_code == 502


def test_kakao_only_user_cannot_login_with_password(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="4004", nickname="카카오전용", email="kakaoonly@test.com"),
    )
    client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )

    res = client.post("/auth/login", json={"email": "kakaoonly@test.com", "password": "anything"})
    assert res.status_code == 401
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k kakao`
Expected: FAIL — `404 Not Found` (엔드포인트 없음)

- [ ] **Step 3: `backend/app/routers/auth.py` 전체를 아래 내용으로 교체**

```python
"""이메일 로그인/회원가입 + 카카오 소셜 로그인. 전화번호는 phone_hash로만 저장."""

import hashlib
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.kakao import KakaoAuthError, exchange_code_for_token, fetch_kakao_user
from app.models import Platform, SocialAccount, Store, StorePlatformConnection, Subscription, User

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str
    phone: str | None = None  # 원문은 저장하지 않고 즉시 해시
    marketing_agreed: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class KakaoCallbackRequest(BaseModel):
    code: str
    redirect_uri: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "has_phone": user.phone_hash is not None,
        "marketing_agreed": user.marketing_agreed,
    }


def _create_default_store_and_subscription(user: User, db: Session) -> None:
    """가입 직후 빈 대시보드를 보여주지 않도록 기본 매장 1개 + 배민 연결 + Basic 구독을 만든다."""
    store = Store(
        user_id=user.id, name="내 매장", category="기타", created_at=datetime.now(timezone.utc)
    )
    db.add(store)
    db.flush()

    baemin = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if baemin:
        db.add(StorePlatformConnection(
            store_id=store.id, platform_id=baemin.id,
            platform_store_id=f"MK-{store.id:08d}", business_number="000-00-00000",
            connected_at=datetime.now(timezone.utc),
        ))
    db.add(Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    phone_hash = hashlib.sha256(body.phone.encode()).hexdigest() if body.phone else None
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        nickname=body.nickname,
        phone_hash=phone_hash,
        marketing_agreed=body.marketing_agreed,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()

    _create_default_store_and_subscription(user, db)
    db.commit()

    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.post("/kakao/callback")
def kakao_callback(body: KakaoCallbackRequest, db: Session = Depends(get_db)):
    try:
        access_token = exchange_code_for_token(body.code, body.redirect_uri)
        kakao_user = fetch_kakao_user(access_token)
    except KakaoAuthError:
        raise HTTPException(502, "카카오 로그인에 실패했습니다")

    social = db.scalar(
        select(SocialAccount).where(
            SocialAccount.provider == "kakao",
            SocialAccount.provider_user_id == kakao_user.id,
        )
    )
    if social:
        user = db.get(User, social.user_id)
    else:
        user = None
        if kakao_user.email:
            user = db.scalar(select(User).where(User.email == kakao_user.email))
        if user is None:
            user = User(
                email=kakao_user.email,
                password_hash=None,
                nickname=kakao_user.nickname,
                marketing_agreed=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
            db.flush()
            _create_default_store_and_subscription(user, db)
        db.add(SocialAccount(
            user_id=user.id, provider="kakao", provider_user_id=kakao_user.id,
            connected_at=datetime.now(timezone.utc),
        ))

    db.commit()
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


class UpdateProfileRequest(BaseModel):
    nickname: str | None = None
    phone: str | None = None  # 원문은 저장하지 않고 즉시 해시
    marketing_agreed: bool | None = None


@router.patch("/me")
def update_me(body: UpdateProfileRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.nickname is not None:
        user.nickname = body.nickname
    if body.phone is not None:
        user.phone_hash = hashlib.sha256(body.phone.encode()).hexdigest()
    if body.marketing_agreed is not None:
        user.marketing_agreed = body.marketing_agreed
    db.commit()
    return _user_dict(user)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v`
Expected: 전체 PASS (기존 테스트 포함, 새 카카오 테스트 5개 포함)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 실행**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (다른 라우터 테스트에 회귀 없음 확인)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/auth.py backend/tests/test_auth.py
git commit -m "feat: POST /auth/kakao/callback 추가 (계정 자동 연결 포함)"
```

---

### Task 4: 프론트엔드 — 카카오 로그인 버튼 + 콜백 페이지

**Files:**
- Modify: `frontend/src/lib/store-context.tsx:7` (`MeResponse.email` nullable)
- Modify: `frontend/src/app/(app)/account/profile/page.tsx:62`
- Modify: `frontend/src/components/Sidebar.tsx:158`
- Create: `frontend/src/lib/kakao.ts`
- Modify: `frontend/src/app/login/page.tsx`
- Create: `frontend/src/app/auth/kakao/callback/page.tsx`
- Create: `frontend/.env.local` (gitignore됨, 커밋 안 함)

**Interfaces:**
- Consumes: 백엔드 `POST /auth/kakao/callback`(Task 3), 기존 `apiPost`/`setToken`/`ApiError`(`@/lib/api`).
- Produces: 없음(최종 UI 계층).

- [ ] **Step 1: `MeResponse.email`을 nullable로, null-safe 렌더링 반영**

`frontend/src/lib/store-context.tsx:7`을 다음으로 교체:

```typescript
type MeResponse = { id: number; email: string | null; nickname: string; has_phone: boolean; marketing_agreed: boolean };
```

`frontend/src/app/(app)/account/profile/page.tsx:62`을 다음으로 교체:

```tsx
              value={user.email ?? "카카오 계정 (이메일 없음)"}
```

`frontend/src/components/Sidebar.tsx:158`을 다음으로 교체:

```tsx
        <p className="truncate text-[11px] text-muted">{user?.email ?? "카카오 계정"}</p>
```

- [ ] **Step 2: 카카오 인가 URL 헬퍼 작성**

`frontend/src/lib/kakao.ts` 신규 생성:

```typescript
export function kakaoRedirectUri(): string {
  return `${window.location.origin}/auth/kakao/callback`;
}

export function kakaoAuthorizeUrl(): string {
  const clientId = process.env.NEXT_PUBLIC_KAKAO_CLIENT_ID ?? "";
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: kakaoRedirectUri(),
    response_type: "code",
  });
  return `https://kauth.kakao.com/oauth/authorize?${params.toString()}`;
}
```

- [ ] **Step 3: 로그인 페이지에 "카카오로 로그인" 버튼 추가**

`frontend/src/app/login/page.tsx:6`(import 줄)을 다음으로 교체:

```tsx
import { ApiError, apiPost, getToken, setToken } from "@/lib/api";
import { kakaoAuthorizeUrl } from "@/lib/kakao";
```

`frontend/src/app/login/page.tsx`의 "데모 계정으로 로그인" 버튼(기존 87-93번째 줄) 바로 다음에 추가:

```tsx
        <button
          type="button"
          onClick={() => {
            window.location.href = kakaoAuthorizeUrl();
          }}
          className="mt-3 w-full rounded-lg bg-[#FEE500] py-2.5 text-sm font-medium text-black transition hover:opacity-90"
        >
          카카오로 로그인
        </button>
```

- [ ] **Step 4: 카카오 콜백 페이지 작성**

`frontend/src/app/auth/kakao/callback/page.tsx` 신규 생성:

```tsx
"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, apiPost, setToken } from "@/lib/api";
import { kakaoRedirectUri } from "@/lib/kakao";

type TokenResponse = { access_token: string };

function KakaoCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const submitted = useRef(false);

  useEffect(() => {
    if (submitted.current) return;
    submitted.current = true;

    const code = searchParams.get("code");
    if (!code) {
      setError("카카오 로그인 코드가 없습니다");
      return;
    }

    apiPost<TokenResponse>("/auth/kakao/callback", { code, redirect_uri: kakaoRedirectUri() })
      .then((res) => {
        setToken(res.access_token);
        router.push("/dashboard");
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "카카오 로그인에 실패했습니다");
      });
  }, [searchParams, router]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 text-center">
        {error ? (
          <>
            <p className="mb-4 text-xs text-danger">{error}</p>
            <a href="/login" className="text-sm text-accent hover:underline">
              로그인으로 돌아가기
            </a>
          </>
        ) : (
          <p className="text-sm text-muted">카카오 로그인 처리 중...</p>
        )}
      </div>
    </main>
  );
}

export default function KakaoCallbackPage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <p className="text-sm text-muted">로딩 중...</p>
        </main>
      }
    >
      <KakaoCallbackInner />
    </Suspense>
  );
}
```

(`useSearchParams`는 빌드 시 Suspense 경계 없이 쓰면 에러가 나서, 내부 컴포넌트를 `Suspense`로 감쌌다. `useRef` 가드는 React 19 개발 모드의 effect 이중 실행으로 `code`가 두 번 소진되는 것을 막는다 — 카카오 인가 코드는 1회용이라 두 번째 요청은 실패한다.)

- [ ] **Step 5: 로컬 카카오 client_id 환경변수 설정**

`frontend/.env.local` 신규 생성 (이미 `.gitignore`에 `.env*`로 무시됨):

```
NEXT_PUBLIC_KAKAO_CLIENT_ID=013b6d77c13fe0a1eb20e41d1bc012d4
```

- [ ] **Step 6: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/lib/kakao.ts frontend/src/lib/store-context.tsx \
  frontend/src/app/login/page.tsx frontend/src/app/auth/kakao/callback/page.tsx \
  "frontend/src/app/(app)/account/profile/page.tsx" frontend/src/components/Sidebar.tsx
git commit -m "feat: 카카오 로그인 버튼 + 콜백 페이지 추가"
```

(`.env.local`은 gitignore 대상이라 커밋되지 않는다 — 의도된 동작.)

---

### Task 5: 로컬 통합 검증 + 배포 안내

**Files:** 없음(실행/검증만).

**Interfaces:** 없음(최종 검증 단계).

- [ ] **Step 1: 로컬 DB에 새 스키마 적용**

Run:
```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql
```
Expected: 에러 없이 완료 (schema.sql이 기존 테이블을 DROP 후 재생성하므로 로컬 데이터는 초기화된다).

- [ ] **Step 2: 백엔드 전체 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 3: 로컬 서버 기동**

Run (각각 별도 터미널):
```bash
cd backend
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/delivery_insight" \
KAKAO_CLIENT_ID="013b6d77c13fe0a1eb20e41d1bc012d4" \
KAKAO_CLIENT_SECRET="<카카오 콘솔 > 카카오 로그인 > 보안의 Client Secret 값>" \
  .venv/bin/uvicorn app.main:app --reload
```

(로컬 검증 중 실제로 확인된 사항: 이 카카오 앱은 Client Secret이 활성화돼 있어서, `KAKAO_CLIENT_SECRET` 없이 토큰 교환을 시도하면 카카오가 `invalid_client`/`KOE010`으로 거부한다. 브리프 작성 시점엔 "콘솔에서 활성화한 경우에만 선택 사용"으로 옵션 취급했지만, 이 프로젝트의 카카오 앱은 필수다.)
```bash
cd frontend
npm run dev
```

- [ ] **Step 4: 브라우저로 실제 카카오 로그인 확인**

`http://localhost:3000/login` 접속 → "카카오로 로그인" 클릭 → 실제 카카오 계정으로 동의 → `/auth/kakao/callback`으로 리다이렉트 → 대시보드 진입까지 직접 확인한다. 대시보드에서 매출/리뷰 등 기본 매장 데이터가 빈 화면 없이 보이는지도 확인한다.

만약 카카오 콘솔에서 "등록되지 않은 Redirect URI" 에러가 나면, 카카오 디벨로퍼스 콘솔의 Redirect URI에 `http://localhost:3000/auth/kakao/callback`이 정확히 등록돼 있는지 확인한다(이전에 사용자가 등록한 값과 일치해야 함).

- [ ] **Step 5: 커밋 (필요 시)**

이 태스크는 코드 변경이 없으므로 보통 커밋할 것이 없다. 검증 중 버그를 발견해 수정했다면 그 수정을 별도로 커밋한다.

- [ ] **Step 6: 배포 안내 (실행은 사용자 확인 후)**

로컬 검증이 끝나면 Railway 배포가 남는다. 이 단계는 프로덕션 환경(공유 상태)을 바꾸는 작업이라 실행 전 사용자에게 반드시 확인받는다:
1. Railway `backend` 서비스 변수에 `KAKAO_CLIENT_ID=013b6d77c13fe0a1eb20e41d1bc012d4`와
   `KAKAO_CLIENT_SECRET=<카카오 콘솔의 Client Secret 값>`을 둘 다 추가한다 — 로컬 검증 중
   이 카카오 앱은 Client Secret이 필수임을 확인했다(없으면 `invalid_client`로 거부됨).
2. Railway `frontend` 서비스 변수에 `NEXT_PUBLIC_KAKAO_CLIENT_ID=013b6d77c13fe0a1eb20e41d1bc012d4` 추가 (Next.js는 이 값을 빌드 시점에 굳히므로 반드시 재배포가 필요하다).
3. 카카오 디벨로퍼스 콘솔의 Redirect URI에 `https://frontend-production-5aa7.up.railway.app/auth/kakao/callback`이 등록돼 있는지 재확인(이미 앞서 등록 요청함).
4. 프로덕션 Postgres 스키마 적용 — **`schema.sql` 전체를 재실행하지 않는다.** 그러면 `DROP TABLE ... CASCADE`로
   기존 데이터(데모 계정 등)가 전부 날아간다. 대신 이번 변경분만 반영하는 아래 3개 문장만 프로덕션에 직접 실행한다
   (최종 리뷰에서 지적됨 — 파괴적 작업이 불필요했음):
   ```sql
   ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
   ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
   CREATE TABLE social_accounts (
       id                BIGSERIAL PRIMARY KEY,
       user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       provider          VARCHAR(20) NOT NULL,
       provider_user_id  VARCHAR(100) NOT NULL,
       connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
       UNIQUE (provider, provider_user_id)
   );
   CREATE INDEX idx_social_accounts_user ON social_accounts(user_id);
   ```
   `schema.sql`은 여전히 로컬 재구축용 전체 정본으로 유지하되, 프로덕션은 이 증분 문장으로만 갱신한다.
   실행 전 반드시 사용자에게 다시 확인받는다.
5. `railway up backend`, `railway up frontend`로 재배포.

## 다음 단계 (이번 계획 범위 밖)

- 카카오 비즈니스 앱 인증 후 이메일 동의 항목 추가.
- 네이버/구글 등 추가 provider.
- 결제/구독 → 실플랫폼 연동 → LLM+RAG 답글 (`CLAUDE.md`의 "방향 전환" 절 순서대로).
