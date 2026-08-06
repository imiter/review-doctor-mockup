# 이메일 회원가입 다단계 인증 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/signup`을 "닉네임/이메일 → 이메일 인증(Resend 실발송) → 휴대폰 번호 → 휴대폰 인증(Mock) → 비밀번호+확인 → 가입 완료" 5단계 위자드로 바꾼다.

**Architecture:** 새 테이블 `signup_verifications` 하나에 이메일/휴대폰 인증 코드를 저장한다. 각 단계의 "확인" 버튼은 가벼운 사전 체크만 하고, 실제 계정 생성은 마지막 `POST /auth/signup`에서 두 코드를 서버가 다시 한번 검증한 뒤에만 이뤄진다. `users` 테이블은 변경하지 않는다 — 계정은 항상 인증 완료 후에만 생성되므로 "미인증 계정" 상태 자체가 없다.

**Tech Stack:** FastAPI, SQLAlchemy, httpx(이미 의존성에 있음, Resend REST API 직접 호출), PostgreSQL(schema.sql), Next.js App Router(클라이언트 state 기반 위자드).

## Global Constraints

- 실제 SMS/카카오톡 발송은 여전히 금지 — 휴대폰 인증은 Mock이다. 서버가 코드를 생성해 API 응답에 그대로 반환하고, 프론트가 화면에 표시한다 (실제 발송 없음).
- 이메일 인증은 실제로 Resend REST API(`httpx`로 직접 호출, SDK 추가 없음)로 발송한다.
- Resend는 커스텀 도메인 미인증 상태라 기본 발신 주소(`onboarding@resend.dev`)로는 **Resend 계정 소유자 본인 이메일 외에는 실제 수신이 제한될 수 있다.** 알려진 제약으로 남겨둔다.
- 전화번호는 원문을 저장하지 않는다 — `signup_verifications.target`에도 휴대폰은 `users.phone_hash`와 동일한 SHA-256 해시로만 저장한다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 `DROP TABLE ... CASCADE` 후 전체 재생성하는 방식의 DB 정본이다. 로컬은 `schema.sql` 전체 재실행, 운영(프로덕션)은 기존 데이터를 지우지 않도록 증분 `CREATE TABLE` 문 하나만 실행한다(Task 5).
- 만료된/방치된 `signup_verifications` 행을 정리하는 배치 작업은 이번 범위에서 만들지 않는다(YAGNI) — 재발송 시 upsert로 덮어써지고, 만료된 행은 검증에서 자연히 거부된다.
- 카카오 소셜 로그인 플로우(`/auth/kakao/callback`)는 이 작업 범위 밖이며 변경하지 않는다.
- 참고 스펙: `docs/superpowers/specs/2026-08-07-email-signup-verification-design.md`

---

### Task 1: DB 스키마 — `signup_verifications` 테이블

**Files:**
- Modify: `schema.sql:4`(테이블 개수 주석), `schema.sql:21-26`(DROP TABLE 목록), `schema.sql:292-294`(social_accounts 인덱스와 COMMIT 사이에 추가)
- Modify: `backend/app/models.py`(파일 끝에 `SignupVerification` 클래스 추가)
- Modify: `CLAUDE.md:70-74`(DB 설계 절), `CLAUDE.md:76-97`("테이블 용도" 목록)
- Modify: `README.md:25,29`(테이블 개수/목록)
- Test: `backend/tests/test_auth.py`(파일 끝에 추가)

**Interfaces:**
- Produces: SQLAlchemy 모델 `SignupVerification(id, target: str, purpose: str, code_hash: str, expires_at: datetime, attempts: int, created_at: datetime)`, `UniqueConstraint("target", "purpose")` — Task 3에서 그대로 가져다 쓴다.

- [ ] **Step 1: `schema.sql` 헤더 주석의 테이블 개수 갱신**

`schema.sql:4`를 다음으로 교체:

```sql
-- 18개 테이블. 모든 FK에 ON DELETE 정책 명시.
```

- [ ] **Step 2: DROP TABLE 목록에 `signup_verifications` 추가**

`schema.sql:21-26`을 다음으로 교체:

```sql
DROP TABLE IF EXISTS
    signup_verifications, social_accounts, alerts, ad_rank_snapshots, ad_performance_metrics,
    ad_campaigns, repurchase_metrics, daily_settlements, review_replies, reviews, orders,
    reply_settings, reply_styles, subscriptions, store_platform_connections,
    platforms, stores, users
CASCADE;
```

- [ ] **Step 3: `signup_verifications` 테이블을 `social_accounts` 다음, `COMMIT` 앞에 추가**

`schema.sql:292`(`CREATE INDEX idx_social_accounts_user ...` 줄)과 `schema.sql:294`(`COMMIT;`) 사이에 삽입:

```sql

-- ----------------------------------------------------------------------------
-- 18. signup_verifications — 이메일 회원가입 인증 코드(이메일 실발송/휴대폰 Mock).
--     users를 참조하지 않는다 — 계정은 인증이 모두 끝난 뒤에만 생성되므로
--     "미인증 계정" 상태 자체가 없다. target은 이메일이면 평문, 휴대폰이면
--     phone_hash와 동일한 SHA-256 해시(전화번호 원문 저장 금지 원칙 유지).
-- ----------------------------------------------------------------------------
CREATE TABLE signup_verifications (
    id         BIGSERIAL PRIMARY KEY,
    target     VARCHAR(255) NOT NULL,
    purpose    VARCHAR(10)  NOT NULL CHECK (purpose IN ('email', 'phone')),
    code_hash  CHAR(64)     NOT NULL,
    expires_at TIMESTAMPTZ  NOT NULL,
    attempts   INT          NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (target, purpose)
);
```

(`UNIQUE (target, purpose)`가 이미 유니크 인덱스를 자동 생성하므로 별도 `CREATE INDEX`는 필요 없다.)

- [ ] **Step 4: `models.py`에 `SignupVerification` 추가**

`backend/app/models.py` 파일 맨 끝에 추가:

```python


class SignupVerification(Base):
    __tablename__ = "signup_verifications"
    __table_args__ = (UniqueConstraint("target", "purpose"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    target: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(10))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]
```

(`UniqueConstraint`는 이미 `backend/app/models.py` 상단에서 `SocialAccount`가 쓰고 있어 import가 이미 돼 있다 — 추가 import 불필요.)

- [ ] **Step 5: 문서의 테이블 개수/목록 갱신**

`CLAUDE.md:70`을 `## DB 설계 (18개 테이블)`로 교체.

`CLAUDE.md:71-74`을 다음으로 교체:

```
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, review_replies, reply_styles, reply_settings,
daily_settlements, repurchase_metrics, ad_campaigns,
ad_performance_metrics, ad_rank_snapshots, alerts, social_accounts,
signup_verifications.
```

`CLAUDE.md:97`(`- social_accounts: ...` 줄) 다음에 추가:

```
- signup_verifications: 이메일 회원가입 인증 코드(이메일 실발송/휴대폰 Mock). users를
  참조하지 않는다 — 인증이 끝난 뒤에만 계정이 생성되기 때문.
```

`README.md:25`를 `## DB 설계 (18개 테이블)`로, `README.md:29`의 목록 끝에 `, signup_verifications`를 추가.

- [ ] **Step 6: 모델 테스트 작성**

`backend/tests/test_auth.py` 파일 끝에 추가:

```python
def test_signup_verification_round_trips(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="model-test@example.com", purpose="email", code_hash="a" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    db_session.commit()

    found = db_session.query(SignupVerification).filter_by(
        target="model-test@example.com", purpose="email"
    ).one()
    assert found.attempts == 0
    assert len(found.code_hash) == 64


def test_signup_verification_unique_target_purpose(db_session):
    from datetime import datetime, timedelta, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="dupe@example.com", purpose="email", code_hash="a" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    db_session.commit()

    db_session.add(SignupVerification(
        target="dupe@example.com", purpose="email", code_hash="b" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 7: 테스트 실행**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k signup_verification`
Expected: 새 테스트 2개 PASS

- [ ] **Step 8: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_auth.py CLAUDE.md README.md
git commit -m "feat: signup_verifications 테이블 추가"
```

---

### Task 2: 인증 코드/Resend 클라이언트 (`backend/app/email_verification.py`)

**Files:**
- Create: `backend/app/email_verification.py`
- Test: `backend/tests/test_email_verification.py`

**Interfaces:**
- Consumes: 없음(외부 `httpx`만 사용).
- Produces: `generate_code() -> str`(6자리 숫자), `hash_code(code: str) -> str`(SHA-256 hex), `send_verification_email(to: str, code: str) -> None`, `EmailSendError(Exception)`, 상수 `EMAIL_CODE_TTL: timedelta`, `PHONE_CODE_TTL: timedelta`, `RESEND_COOLDOWN: timedelta`, `MAX_ATTEMPTS: int` — Task 3에서 이름 그대로 import해 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_email_verification.py` 신규 생성:

```python
import httpx
import pytest

from app import email_verification as ev


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_generate_code_is_six_digits():
    code = ev.generate_code()
    assert len(code) == 6
    assert code.isdigit()


def test_hash_code_is_deterministic_sha256():
    assert ev.hash_code("123456") == ev.hash_code("123456")
    assert ev.hash_code("123456") != ev.hash_code("654321")
    assert len(ev.hash_code("123456")) == 64


def test_send_verification_email_success(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    def fake_post(url, headers, json, timeout):
        assert url == ev._RESEND_URL
        assert headers["Authorization"] == "Bearer test-key"
        assert json["to"] == "user@example.com"
        assert "482913" in json["html"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913")  # 예외 없이 통과하면 성공


def test_send_verification_email_raises_on_api_error(monkeypatch):
    monkeypatch.setattr(ev.httpx, "post", lambda url, headers, json, timeout: _FakeResponse(422))

    with pytest.raises(ev.EmailSendError):
        ev.send_verification_email("user@example.com", "482913")


def test_send_verification_email_wraps_network_error(monkeypatch):
    def _raise(url, headers, json, timeout):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(ev.httpx, "post", _raise)

    with pytest.raises(ev.EmailSendError):
        ev.send_verification_email("user@example.com", "482913")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_email_verification.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.email_verification'`

- [ ] **Step 3: `backend/app/email_verification.py` 구현**

```python
"""이메일 회원가입 인증 — 6자리 코드 생성·해시 + Resend 실발송.

휴대폰 인증은 이 모듈에서 발송 함수를 제공하지 않는다 — CLAUDE.md 원칙상 실제
SMS 발송이 금지돼 있어 Mock이다. 라우터가 generate_code()로 코드를 만들어 API
응답에 그대로 돌려주고, 실제로는 아무 곳에도 전송하지 않는다.
"""

import hashlib
import os
import secrets
from datetime import timedelta

import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "onboarding@resend.dev")

_RESEND_URL = "https://api.resend.com/emails"

EMAIL_CODE_TTL = timedelta(minutes=10)
PHONE_CODE_TTL = timedelta(minutes=5)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


class EmailSendError(Exception):
    pass


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def send_verification_email(to: str, code: str) -> None:
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": EMAIL_FROM_ADDRESS,
                "to": to,
                "subject": "[Delivery Review] 이메일 인증번호",
                "html": f"<p>인증번호: <b>{code}</b> (10분 이내 입력해주세요)</p>",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise EmailSendError(f"이메일 발송 요청 실패: {e}") from e
    if resp.status_code >= 400:
        raise EmailSendError(f"이메일 발송 실패: {resp.status_code}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_email_verification.py -v`
Expected: 5개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/email_verification.py backend/tests/test_email_verification.py
git commit -m "feat: 이메일 인증 코드 생성/해시 + Resend 발송 클라이언트 추가"
```

---

### Task 3: 백엔드 — `/auth/signup/*` 엔드포인트 + 최종 가입 재검증

**Files:**
- Modify: `backend/app/routers/auth.py`(전체 재작성)
- Modify: `backend/tests/conftest.py`(파일 끝에 `signup_flow` 픽스처 추가)
- Modify: `backend/tests/test_auth.py`(기존 signup 테스트 3개 교체 + 신규 테스트 추가)

**Interfaces:**
- Consumes: Task 1의 `SignupVerification`. Task 2의 `generate_code`, `hash_code`, `send_verification_email`, `EmailSendError`, `EMAIL_CODE_TTL`, `PHONE_CODE_TTL`, `RESEND_COOLDOWN`, `MAX_ATTEMPTS`.
- Produces: `POST /auth/signup/email-code` `{email}` → `{"sent": true}`. `POST /auth/signup/verify-email-code` `{email, code}` → `{"verified": true}`. `POST /auth/signup/phone-code` `{phone}` → `{"mock_code": str}`. `POST /auth/signup/verify-phone-code` `{phone, code}` → `{"verified": true}`. `POST /auth/signup` `{email, email_code, phone, phone_code, password, nickname, marketing_agreed}` → 기존과 동일한 `TokenResponse`.

- [ ] **Step 1: 기존 signup 테스트가 새 필수 필드 없이 깨지는 것부터 확인**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k signup`
Expected: 아직 코드를 안 건드렸으므로 PASS (베이스라인 확인용 — 이 스텝은 회귀 여부를 나중에 비교하기 위한 것이다).

- [ ] **Step 2: `backend/app/routers/auth.py` 전체를 아래 내용으로 교체**

```python
"""이메일 로그인/회원가입(다단계 인증) + 카카오 소셜 로그인. 전화번호는 phone_hash로만 저장."""

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.email_verification import (
    EMAIL_CODE_TTL,
    MAX_ATTEMPTS,
    PHONE_CODE_TTL,
    RESEND_COOLDOWN,
    EmailSendError,
    generate_code,
    hash_code,
    send_verification_email,
)
from app.kakao import KakaoAuthError, exchange_code_for_token, fetch_kakao_user
from app.models import SignupVerification, SocialAccount, Store, Subscription, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class EmailCodeRequest(BaseModel):
    email: EmailStr


class VerifyEmailCodeRequest(BaseModel):
    email: EmailStr
    code: str


class PhoneCodeRequest(BaseModel):
    phone: str


class VerifyPhoneCodeRequest(BaseModel):
    phone: str
    code: str


class SignupRequest(BaseModel):
    email: EmailStr
    email_code: str
    phone: str
    phone_code: str
    password: str
    nickname: str
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
    """가입 직후 기본 매장 1개 + Basic 구독을 만든다. 플랫폼 연결은 "가게 연결" 화면에서
    사용자가 직접 해야 한다 — 데모에서 그 과정을 보여줘야 하므로 자동 연결하지 않는다."""
    store = Store(
        user_id=user.id, name="내 매장", category="기타", created_at=datetime.now(timezone.utc)
    )
    db.add(store)
    db.flush()

    db.add(Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()


def _as_aware(dt: datetime) -> datetime:
    """SQLite(테스트)는 datetime을 tz 정보 없이 되돌려줄 수 있어서, naive면 UTC로
    간주한다. Postgres(TIMESTAMPTZ, 운영)는 항상 tz-aware라 이 변환이 필요 없다."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get_verification(db: Session, target: str, purpose: str) -> SignupVerification | None:
    return db.scalar(
        select(SignupVerification).where(
            SignupVerification.target == target, SignupVerification.purpose == purpose
        )
    )


def _issue_code(db: Session, target: str, purpose: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    existing = _get_verification(db, target, purpose)
    if existing and _as_aware(existing.created_at) > now - RESEND_COOLDOWN:
        raise HTTPException(429, "잠시 후 다시 시도해주세요")

    code = generate_code()
    if existing:
        existing.code_hash = hash_code(code)
        existing.expires_at = now + ttl
        existing.attempts = 0
        existing.created_at = now
    else:
        db.add(SignupVerification(
            target=target, purpose=purpose, code_hash=hash_code(code),
            expires_at=now + ttl, attempts=0, created_at=now,
        ))
    db.commit()
    return code


def _check_code(db: Session, target: str, purpose: str, code: str) -> None:
    row = _get_verification(db, target, purpose)
    if row is None or _as_aware(row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(400, "인증번호가 만료되었습니다. 다시 받아주세요")
    if row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(400, "시도 횟수를 초과했습니다. 인증번호를 다시 받아주세요")
    if row.code_hash != hash_code(code):
        row.attempts += 1
        db.commit()
        raise HTTPException(400, "인증번호가 올바르지 않습니다")


@router.post("/signup/email-code")
def request_email_code(body: EmailCodeRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    code = _issue_code(db, body.email, "email", EMAIL_CODE_TTL)
    try:
        send_verification_email(body.email, code)
    except EmailSendError as e:
        logger.warning("이메일 발송 실패 (%s): %s", body.email, e)
        raise HTTPException(502, "이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요")
    return {"sent": True}


@router.post("/signup/verify-email-code")
def verify_email_code(body: VerifyEmailCodeRequest, db: Session = Depends(get_db)):
    _check_code(db, body.email, "email", body.code)
    return {"verified": True}


@router.post("/signup/phone-code")
def request_phone_code(body: PhoneCodeRequest, db: Session = Depends(get_db)):
    code = _issue_code(db, _hash_phone(body.phone), "phone", PHONE_CODE_TTL)
    return {"mock_code": code}


@router.post("/signup/verify-phone-code")
def verify_phone_code(body: VerifyPhoneCodeRequest, db: Session = Depends(get_db)):
    _check_code(db, _hash_phone(body.phone), "phone", body.code)
    return {"verified": True}


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    phone_hash = _hash_phone(body.phone)
    _check_code(db, body.email, "email", body.email_code)
    _check_code(db, phone_hash, "phone", body.phone_code)

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

    db.execute(delete(SignupVerification).where(SignupVerification.target.in_([body.email, phone_hash])))
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
        if user is None:
            raise HTTPException(401, "사용자를 찾을 수 없습니다")
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
        user.phone_hash = _hash_phone(body.phone)
    if body.marketing_agreed is not None:
        user.marketing_agreed = body.marketing_agreed
    db.commit()
    return _user_dict(user)
```

- [ ] **Step 3: `backend/tests/conftest.py`에 `signup_flow` 픽스처 추가**

파일 끝에 추가:

```python


@pytest.fixture()
def signup_flow(client, monkeypatch):
    """이메일/휴대폰 인증 코드를 고정값("123456")으로 monkeypatch해서 /auth/signup까지
    한 번에 통과시켜주는 헬퍼. 실제 이메일 발송은 no-op으로 막는다."""
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)

    def _run(email: str, phone: str = "010-1234-5678", **overrides):
        client.post("/auth/signup/email-code", json={"email": email})
        client.post("/auth/signup/phone-code", json={"phone": phone})
        payload = {
            "email": email, "email_code": "123456", "phone": phone, "phone_code": "123456",
            "password": "pw12345!", "nickname": "테스트", "marketing_agreed": False,
        }
        payload.update(overrides)
        return client.post("/auth/signup", json=payload)

    return _run
```

- [ ] **Step 4: 기존 signup 테스트 3개를 새 플로우에 맞게 교체**

`backend/tests/test_auth.py`의 맨 위 3개 테스트(`test_signup_creates_user_store_and_subscription`, `test_phone_never_stored_raw`, `test_duplicate_signup_rejected`, 대략 1~35번째 줄)를 통째로 아래로 교체:

```python
def test_signup_creates_user_store_and_subscription(client, platforms, signup_flow):
    res = signup_flow("new@test.com", phone="010-1234-5678", nickname="새사장", marketing_agreed=True)
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "new@test.com"
    assert "access_token" in body

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["nickname"] == "새사장"

    stores = client.get("/stores", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert len(stores) == 1  # 가입 직후 빈 대시보드 방지용 기본 매장


def test_phone_never_stored_raw(client, platforms, db_session, signup_flow):
    from app.models import User

    signup_flow("phonecheck@test.com", phone="010-9999-0000")
    user = db_session.query(User).filter_by(email="phonecheck@test.com").one()
    assert user.phone_hash is not None
    assert user.phone_hash != "010-9999-0000"
    assert len(user.phone_hash) == 64  # SHA-256 hex


def test_duplicate_signup_rejected(client, platforms, signup_flow):
    signup_flow("dup@test.com")
    res = signup_flow("dup@test.com")
    assert res.status_code == 409
```

(이 세 테스트만 바꾸고, 그 아래 `test_login_wrong_password_rejected`부터 파일 끝까지는 그대로 둔다.)

- [ ] **Step 5: 새 엔드포인트 테스트 추가**

`backend/tests/test_auth.py` 파일 끝(Task 1에서 추가한 `test_signup_verification_*` 다음)에 추가:

```python
def test_email_code_rejects_already_registered_email(client, seeded_user):
    res = client.post("/auth/signup/email-code", json={"email": "demo@dris.kr"})
    assert res.status_code == 409


def test_email_code_resend_cooldown(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)

    first = client.post("/auth/signup/email-code", json={"email": "cooldown@test.com"})
    assert first.status_code == 200
    second = client.post("/auth/signup/email-code", json={"email": "cooldown@test.com"})
    assert second.status_code == 429


def test_email_code_send_failure_returns_502(client, platforms, monkeypatch):
    from app.email_verification import EmailSendError
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")

    def _raise(to, code):
        raise EmailSendError("boom")

    monkeypatch.setattr(auth_router, "send_verification_email", _raise)

    res = client.post("/auth/signup/email-code", json={"email": "fail@test.com"})
    assert res.status_code == 502


def test_verify_email_code_wrong_code_rejected(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "wrongcode@test.com"})

    res = client.post("/auth/signup/verify-email-code", json={"email": "wrongcode@test.com", "code": "000000"})
    assert res.status_code == 400


def test_verify_email_code_exceeds_max_attempts(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "toomany@test.com"})

    for _ in range(5):
        res = client.post("/auth/signup/verify-email-code", json={"email": "toomany@test.com", "code": "000000"})
        assert res.status_code == 400

    res = client.post("/auth/signup/verify-email-code", json={"email": "toomany@test.com", "code": "123456"})
    assert res.status_code == 400
    assert "초과" in res.json()["detail"]


def test_phone_code_returns_mock_code_directly(client, platforms):
    res = client.post("/auth/signup/phone-code", json={"phone": "010-1111-2222"})
    assert res.status_code == 200
    body = res.json()
    assert "mock_code" in body
    assert len(body["mock_code"]) == 6


def test_verify_phone_code_success(client, platforms):
    sent = client.post("/auth/signup/phone-code", json={"phone": "010-3333-4444"}).json()
    res = client.post("/auth/signup/verify-phone-code", json={"phone": "010-3333-4444", "code": sent["mock_code"]})
    assert res.status_code == 200


def test_signup_rejects_wrong_email_code(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "badflow@test.com"})
    client.post("/auth/signup/phone-code", json={"phone": "010-5555-1111"})

    res = client.post("/auth/signup", json={
        "email": "badflow@test.com", "email_code": "000000",
        "phone": "010-5555-1111", "phone_code": "123456",
        "password": "pw12345!", "nickname": "실패", "marketing_agreed": False,
    })
    assert res.status_code == 400


def test_signup_rejects_wrong_phone_code(client, platforms, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)
    client.post("/auth/signup/email-code", json={"email": "badflow2@test.com"})
    client.post("/auth/signup/phone-code", json={"phone": "010-5555-2222"})

    res = client.post("/auth/signup", json={
        "email": "badflow2@test.com", "email_code": "123456",
        "phone": "010-5555-2222", "phone_code": "000000",
        "password": "pw12345!", "nickname": "실패", "marketing_agreed": False,
    })
    assert res.status_code == 400


def test_signup_consumes_verification_rows_on_success(client, platforms, db_session, signup_flow):
    from app.models import SignupVerification

    signup_flow("consumed@test.com", phone="010-7777-8888")
    remaining = db_session.query(SignupVerification).filter(
        SignupVerification.target.in_(["consumed@test.com"])
    ).count()
    assert remaining == 0
```

- [ ] **Step 6: 전체 백엔드 테스트 실행**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (다른 라우터 테스트에 회귀 없음 확인, 카카오 테스트도 그대로 통과해야 함)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/auth.py backend/tests/conftest.py backend/tests/test_auth.py
git commit -m "feat: /auth/signup 다단계 인증(이메일 실발송 + 휴대폰 Mock) 엔드포인트 추가"
```

---

### Task 4: 프론트엔드 — 회원가입 5단계 위자드

**Files:**
- Modify: `frontend/src/app/signup/page.tsx`(전체 재작성)

**Interfaces:**
- Consumes: Task 3의 `POST /auth/signup/email-code`, `POST /auth/signup/verify-email-code`, `POST /auth/signup/phone-code`(응답에 `mock_code` 포함), `POST /auth/signup/verify-phone-code`, `POST /auth/signup`. 기존 `apiPost`, `setToken`, `ApiError`(`@/lib/api`).
- Produces: 없음(최종 UI 계층).

- [ ] **Step 1: `frontend/src/app/signup/page.tsx` 전체를 아래 내용으로 교체**

```tsx
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiPost, setToken } from "@/lib/api";

type Step = "email" | "email-code" | "phone" | "phone-code" | "password";
const STEPS: Step[] = ["email", "email-code", "phone", "phone-code", "password"];

type TokenResponse = { access_token: string };

export default function SignupPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [nickname, setNickname] = useState("");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneCode, setPhoneCode] = useState("");
  const [mockPhoneCode, setMockPhoneCode] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [marketingAgreed, setMarketingAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(timer);
  }, [cooldown]);

  const goBack = () => {
    setError(null);
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  };

  const sendEmailCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/email-code", { email });
      setEmailCode("");
      setStep("email-code");
      setCooldown(60);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발송에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const confirmEmailCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/verify-email-code", { email, code: emailCode });
      setStep("phone");
      setCooldown(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호가 올바르지 않습니다");
    } finally {
      setLoading(false);
    }
  };

  const sendPhoneCode = async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<{ mock_code: string }>("/auth/signup/phone-code", { phone });
      setMockPhoneCode(res.mock_code);
      setPhoneCode("");
      setStep("phone-code");
      setCooldown(60);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발급에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const confirmPhoneCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/verify-phone-code", { phone, code: phoneCode });
      setStep("password");
      setCooldown(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호가 올바르지 않습니다");
    } finally {
      setLoading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== passwordConfirm) {
      setError("비밀번호가 일치하지 않습니다");
      return;
    }
    setLoading(true);
    try {
      const res = await apiPost<TokenResponse>("/auth/signup", {
        email,
        email_code: emailCode,
        phone,
        phone_code: phoneCode,
        password,
        nickname,
        marketing_agreed: marketingAgreed,
      });
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "회원가입에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const stepIndex = STEPS.indexOf(step);

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6">
          <p className="text-base font-semibold">회원가입</p>
          <p className="text-xs text-muted">이메일 로그인만 지원합니다 (소셜 로그인은 별도)</p>
          <div className="mt-3 flex gap-1.5">
            {STEPS.map((s, i) => (
              <div key={s} className={`h-1 flex-1 rounded-full ${i <= stepIndex ? "bg-accent" : "bg-surface-2"}`} />
            ))}
          </div>
        </div>

        {error && <p className="mb-4 text-xs text-danger">{error}</p>}

        {step === "email" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">닉네임</label>
              <input
                required autoFocus value={nickname} onChange={(e) => setNickname(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="김사장"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">이메일</label>
              <input
                type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="you@store.com"
              />
            </div>
            <button
              type="button" disabled={loading || !nickname || !email}
              onClick={sendEmailCode}
              className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "발송 중..." : "인증번호 받기"}
            </button>
          </div>
        )}

        {step === "email-code" && (
          <div className="space-y-4">
            <p className="text-xs text-muted">{email}(으)로 인증번호를 보냈습니다.</p>
            <div>
              <label className="mb-1 block text-xs text-muted">이메일 인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={emailCode} onChange={(e) => setEmailCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || emailCode.length !== 6}
                onClick={confirmEmailCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "확인 중..." : "확인"}
              </button>
            </div>
            <button
              type="button" disabled={cooldown > 0 || loading} onClick={sendEmailCode}
              className="w-full text-center text-xs text-accent hover:underline disabled:text-muted disabled:no-underline"
            >
              {cooldown > 0 ? `재전송 (${cooldown}초 후 가능)` : "인증번호 재전송"}
            </button>
          </div>
        )}

        {step === "phone" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">휴대폰 번호</label>
              <input
                required autoFocus value={phone} onChange={(e) => setPhone(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="010-0000-0000"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || !phone}
                onClick={sendPhoneCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "발급 중..." : "인증번호 받기"}
              </button>
            </div>
          </div>
        )}

        {step === "phone-code" && (
          <div className="space-y-4">
            <p className="rounded-lg border border-accent/40 bg-accent-soft px-3 py-2 text-xs text-accent">
              데모용 인증번호: <b>{mockPhoneCode}</b> (Mock — 실제 문자는 발송되지 않습니다)
            </p>
            <div>
              <label className="mb-1 block text-xs text-muted">휴대폰 인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={phoneCode} onChange={(e) => setPhoneCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || phoneCode.length !== 6}
                onClick={confirmPhoneCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "확인 중..." : "확인"}
              </button>
            </div>
            <button
              type="button" disabled={cooldown > 0 || loading} onClick={sendPhoneCode}
              className="w-full text-center text-xs text-accent hover:underline disabled:text-muted disabled:no-underline"
            >
              {cooldown > 0 ? `재전송 (${cooldown}초 후 가능)` : "인증번호 재전송"}
            </button>
          </div>
        )}

        {step === "password" && (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호</label>
              <input
                type="password" required minLength={8} autoFocus
                value={password} onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="8자 이상"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호 확인</label>
              <input
                type="password" required minLength={8}
                value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="비밀번호를 한 번 더 입력해주세요"
              />
            </div>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox" checked={marketingAgreed}
                onChange={(e) => setMarketingAgreed(e.target.checked)}
                className="accent-accent"
              />
              마케팅 정보 수신에 동의합니다 (선택)
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="submit" disabled={loading}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "가입 중..." : "가입 완료"}
              </button>
            </div>
          </form>
        )}

        <p className="mt-6 text-center text-xs text-muted">
          이미 계정이 있나요?{" "}
          <Link href="/login" className="text-accent hover:underline">
            로그인
          </Link>
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/app/signup/page.tsx
git commit -m "feat: 회원가입을 이메일/휴대폰 인증 포함 5단계 위자드로 개편"
```

---

### Task 5: 문서/환경변수 갱신 + 로컬 검증 + 배포 안내

**Files:**
- Modify: `CLAUDE.md`(예외 절 추가, "추가 합의 사항" 갱신)
- Modify: `backend/.env.example`(환경변수 추가)
- Modify: `README.md`(로그인 관련 서술이 있다면 반영 — 없으면 생략)

**Interfaces:** 없음(문서 + 검증 + 배포 단계).

- [ ] **Step 1: `CLAUDE.md`에 "이메일 인증 (실제 발송, 예외 허용)" 절 추가**

`CLAUDE.md:51`(카카오 소셜 로그인 절이 `docs/superpowers/specs/2026-08-06-kakao-social-login-design.md` 참고로 끝나는 줄)과 `CLAUDE.md:53`(`### 모바일 앱 (예외 허용)`) 사이에 삽입:

```markdown

### 이메일 인증 (실제 발송, 예외 허용)
원래 이메일 회원가입은 이메일/비밀번호만 받고 즉시 계정을 만들었으나, 실 SaaS
포트폴리오 데모에 맞게 다단계 인증 위자드로 바꿨다: 이메일 인증(Resend로 실제
발송) → 휴대폰 인증 → 비밀번호+확인 → 가입 완료. 휴대폰 인증은 "절대 금지"의
실제 문자 발송 금지 원칙을 그대로 지켜 Mock이다 — 서버가 인증번호를 생성해 API
응답에 그대로 돌려주고 화면에 표시할 뿐, 실제로 어디에도 전송하지 않는다.
이메일은 애초에 금지 목록에 없던 항목이라 이번에 실제로 붙였다. Resend
커스텀 도메인은 아직 인증하지 않아서 기본 발신 주소(`onboarding@resend.dev`)로는
Resend 계정 소유자 본인 이메일 외에는 실제 수신이 제한될 수 있다는 점은 알려진
제약으로 남겨둔다. 설계 상세는
`docs/superpowers/specs/2026-08-07-email-signup-verification-design.md` 참고.
```

- [ ] **Step 2: "추가 합의 사항"의 로그인 서술 갱신**

`CLAUDE.md:177-178`을 다음으로 교체:

```
- 로그인: 이메일 기반 로그인(이메일/휴대폰 인증 위자드 포함) + 카카오 소셜
  로그인 병행 (네이버/구글/애플은 아직 범위 밖). 상세는 위 "카카오 소셜 로그인",
  "이메일 인증" 절 참고.
```

- [ ] **Step 3: `backend/.env.example`에 이메일 발송 환경변수 추가**

`backend/.env.example`의 `KAKAO_CLIENT_SECRET=` 줄 다음에 추가:

```

# 이메일 회원가입 인증 발송 (Resend). 커스텀 도메인 미인증 시 onboarding@resend.dev
# 발신은 Resend 계정 소유자 본인 이메일 외에는 실제 수신이 제한될 수 있다.
RESEND_API_KEY=
EMAIL_FROM_ADDRESS=onboarding@resend.dev
```

- [ ] **Step 4: 커밋**

```bash
git add CLAUDE.md backend/.env.example
git commit -m "docs: 이메일 인증 예외 허용 절 + RESEND_API_KEY 환경변수 문서화"
```

- [ ] **Step 5: 로컬 DB에 새 스키마 적용**

Run:
```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql
```
Expected: 에러 없이 완료 (schema.sql이 기존 테이블을 DROP 후 재생성하므로 로컬 데이터는 초기화된다). 만약 5432 포트가 이미 다른 컨테이너에 점유돼 있으면(로컬 환경에 흔한 상황), `docker compose`가 쓰는 서비스명 `db`를 그대로 쓰되 충돌 중인 컨테이너를 먼저 내리거나, 임시로 다른 포트에 컨테이너를 띄우고 `DATABASE_URL`을 그에 맞게 바꿔서 검증해도 무방하다.

- [ ] **Step 6: 백엔드 전체 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 7: 로컬 서버 기동 (실제 Resend API 키 필요)**

Resend 대시보드(resend.com)에서 API 키를 발급받아야 한다 — 이 스텝은 사용자가
직접 키를 발급하고 아래 커맨드에 채워 넣어야 진행 가능하다.

```bash
cd backend
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/delivery_insight" \
RESEND_API_KEY="<Resend 대시보드에서 발급받은 API 키>" \
EMAIL_FROM_ADDRESS="onboarding@resend.dev" \
  .venv/bin/uvicorn app.main:app --reload
```
```bash
cd frontend
npm run dev
```

- [ ] **Step 8: 브라우저로 실제 5단계 위자드 확인**

`http://localhost:3000/signup` 접속 → 닉네임 + **Resend 계정 소유자 본인 이메일**
입력(다른 이메일은 커스텀 도메인 인증 전이라 실제 수신이 안 될 수 있음) →
"인증번호 받기" → 실제 수신함에서 6자리 코드 확인 후 입력 → 휴대폰 번호 입력 →
"인증번호 받기" → 화면에 뜨는 Mock 코드를 직접 입력 → 비밀번호+확인 입력 →
"가입 완료" → 대시보드 진입까지 확인한다. 재전송 쿨다운 카운트다운도 눈으로
확인한다.

- [ ] **Step 9: 커밋 (필요 시)**

이 태스크는 검증 단계라 보통 코드 변경이 없다. 검증 중 버그를 발견해 수정했다면
그 수정을 별도로 커밋한다.

- [ ] **Step 10: 배포 안내 (실행은 사용자 확인 후)**

로컬 검증이 끝나면 Railway 배포가 남는다. 프로덕션(공유 상태)을 바꾸는 작업이라
실행 전 반드시 사용자에게 확인받는다:

1. Railway `backend` 서비스 변수에 `RESEND_API_KEY`와 `EMAIL_FROM_ADDRESS`를
   추가한다 (`EMAIL_FROM_ADDRESS`는 커스텀 도메인을 인증하기 전까지
   `onboarding@resend.dev`로 둔다).
2. 프로덕션 Postgres에 아래 증분 SQL 한 문장만 실행한다 — `schema.sql` 전체를
   재실행하면 `DROP TABLE ... CASCADE`로 기존 데이터(카카오 계정, 데모 계정 등)가
   전부 날아가므로 절대 재실행하지 않는다:
   ```sql
   CREATE TABLE signup_verifications (
       id         BIGSERIAL PRIMARY KEY,
       target     VARCHAR(255) NOT NULL,
       purpose    VARCHAR(10)  NOT NULL CHECK (purpose IN ('email', 'phone')),
       code_hash  CHAR(64)     NOT NULL,
       expires_at TIMESTAMPTZ  NOT NULL,
       attempts   INT          NOT NULL DEFAULT 0,
       created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
       UNIQUE (target, purpose)
   );
   ```
   실행 전 반드시 사용자에게 다시 확인받는다.
3. `backend`, `frontend` 두 서비스를 재배포한다. 이번 세션에서 확인된 방법:
   `railway up`(CLI)은 모노레포 루트를 통째로 스캔해버리는 문제가 있었으므로,
   Railway MCP의 `deploy` 도구에 `path`를 `backend/`, `frontend/`로 각각 명시해서
   호출하는 방식이 안정적으로 동작했다. 배포 후 `list_deployments`로 상태가
   `SUCCESS`가 될 때까지 반드시 확인한다 — `FAILED`/`BUILDING`을 성공으로
   보고하지 않는다.

## 다음 단계 (이번 계획 범위 밖)

- Resend 커스텀 도메인 인증 (타인 이메일로도 실제 수신 가능하게).
- 비밀번호 재설정/이메일 변경 시 재인증 플로우.
- 만료된 `signup_verifications` 행 정리 배치(TTL cron) — 현재는 방치해도 무해해서 범위 밖.
- 결제/구독 → 실플랫폼 연동 → LLM+RAG 답글 (`CLAUDE.md`의 "방향 전환" 절 순서대로).
