# 비밀번호 찾기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그인 화면에 "비밀번호를 잊으셨나요?" 링크를 추가하고, 이메일 인증번호로 본인 확인 후 비밀번호를 재설정할 수 있게 한다.

**Architecture:** 회원가입 이메일 인증에서 이미 만든 `signup_verifications` 테이블과 `_issue_code`/`_check_code` 헬퍼(`backend/app/routers/auth.py`)를 그대로 재사용한다. `purpose` 컬럼에 `'password_reset'` 값을 추가로 허용하도록 DB 제약만 넓히고, 새 엔드포인트 2개(`/auth/password-reset/request`, `/auth/password-reset/confirm`)를 추가한다. 프론트는 로그인 화면에 링크 하나, 새 페이지 하나를 추가한다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Resend(이미 연동됨), Next.js App Router.

## Global Constraints

- 이메일 기반 로그인 계정(`password_hash IS NOT NULL`)만 대상. 카카오 전용 계정(`password_hash IS NULL`)은 "카카오로 가입된 계정입니다. 카카오 로그인을 이용해주세요"로 명확히 안내한다.
- 가입되지 않은 이메일도 "가입된 계정이 없습니다"로 명확히 안내한다 — 이 프로젝트는 이메일 열거(enumeration) 방어 정책을 쓰지 않는다(회원가입의 이메일 중복 체크와 동일한 기존 방침).
- 재설정 성공 후 자동 로그인하지 않는다 — 로그인 화면(`/login?reset=success`)으로 돌려보내 새 비밀번호로 직접 로그인하게 한다.
- 새 테이블을 만들지 않는다 — `signup_verifications`의 `purpose` 제약을 확장해서 재사용한다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 전체 재생성 방식의 DB 정본이다. 로컬은 `schema.sql` 전체 재실행, 운영(프로덕션)은 증분 `ALTER TABLE`만 실행한다.
- 참고 스펙: `docs/superpowers/specs/2026-08-07-password-reset-design.md`

---

### Task 1: DB — `purpose` 컬럼/제약 확장 + 이메일 발송 문구 분기

**Files:**
- Modify: `schema.sql:295-309` (signup_verifications 테이블 주석 + `purpose` 컬럼)
- Modify: `backend/app/models.py` (`SignupVerification.purpose` 컬럼 길이)
- Modify: `backend/app/email_verification.py` (`send_verification_email`에 `purpose` 파라미터 추가)
- Test: `backend/tests/test_email_verification.py`, `backend/tests/test_auth.py`(파일 끝에 모델 테스트 추가)

**Interfaces:**
- Produces: `send_verification_email(to: str, code: str, purpose: str = "signup") -> None` — Task 2가 `purpose="password_reset"`으로 호출한다. `signup_verifications.purpose` 컬럼이 `'email' | 'phone' | 'password_reset'`을 저장할 수 있게 된다(Task 2의 `_issue_code`/`_check_code` 호출이 그대로 이 확장된 값을 쓴다 — 헬퍼 함수 자체는 이미 `purpose`를 임의 문자열로 다루므로 변경 없음).

- [ ] **Step 1: `schema.sql`의 `signup_verifications` 주석과 `purpose` 컬럼 갱신**

`schema.sql:295-298`(주석 블록)을 다음으로 교체:

```sql
-- 18. signup_verifications — 이메일 인증 코드. 회원가입 이메일 인증(Resend 실발송/
--     휴대폰 Mock, 휴대폰 인증은 현재 가입 위자드에서 빠져 있어 미사용)과 비밀번호
--     재설정 인증에 공용으로 쓰인다. users를 참조하지 않는다 — 회원가입은 인증이
--     모두 끝난 뒤에만 계정이 생성되고, 비밀번호 재설정은 기존 계정을 이메일로
--     조회할 뿐 이 테이블에 FK로 묶을 필요가 없다. target은 이메일이면 평문,
--     휴대폰이면 phone_hash와 동일한 SHA-256 해시(전화번호 원문 저장 금지 원칙 유지).
```

`schema.sql:303`을 다음으로 교체:

```sql
    purpose    VARCHAR(20)  NOT NULL CHECK (purpose IN ('email', 'phone', 'password_reset')),
```

(기존 `VARCHAR(10)`으로는 `'password_reset'`(14자)이 들어가지 않는다 — 길이도 함께 넓힌다.)

- [ ] **Step 2: `models.py`의 `SignupVerification.purpose` 컬럼 길이 갱신**

`backend/app/models.py`에서 `class SignupVerification` 안의 `purpose: Mapped[str] = mapped_column(String(10))`를 다음으로 교체:

```python
    purpose: Mapped[str] = mapped_column(String(20))
```

- [ ] **Step 3: 실패하는 테스트 작성 — 이메일 발송 문구 분기**

`backend/tests/test_email_verification.py` 파일 끝에 추가:

```python
def test_send_verification_email_password_reset_uses_different_subject(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["subject"] = json["subject"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913", purpose="password_reset")
    assert captured["subject"] == "[Delivery Review] 비밀번호 재설정 인증번호"


def test_send_verification_email_defaults_to_signup_subject(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["subject"] = json["subject"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913")
    assert captured["subject"] == "[Delivery Review] 이메일 인증번호"
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_email_verification.py -v -k subject`
Expected: FAIL — `TypeError: send_verification_email() got an unexpected keyword argument 'purpose'`

- [ ] **Step 5: `backend/app/email_verification.py`에 `purpose` 파라미터 구현**

`send_verification_email` 함수 전체를 다음으로 교체:

```python
_SUBJECTS = {
    "signup": "[Delivery Review] 이메일 인증번호",
    "password_reset": "[Delivery Review] 비밀번호 재설정 인증번호",
}


def send_verification_email(to: str, code: str, purpose: str = "signup") -> None:
    subject = _SUBJECTS.get(purpose, _SUBJECTS["signup"])
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": EMAIL_FROM_ADDRESS,
                "to": to,
                "subject": subject,
                "html": f"<p>인증번호: <b>{code}</b> (10분 이내 입력해주세요)</p>",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise EmailSendError(f"이메일 발송 요청 실패: {e}") from e
    if resp.status_code >= 400:
        raise EmailSendError(f"이메일 발송 실패: {resp.status_code}")
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_email_verification.py -v`
Expected: 7개 테스트 전부 PASS (기존 5개 + 새 2개)

- [ ] **Step 7: 모델 컬럼 길이 확장을 검증하는 테스트 추가**

`backend/tests/test_auth.py` 파일 끝에 추가:

```python
def test_signup_verification_accepts_password_reset_purpose(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models import SignupVerification

    now = datetime.now(timezone.utc)
    db_session.add(SignupVerification(
        target="reset-target@example.com", purpose="password_reset", code_hash="a" * 64,
        expires_at=now + timedelta(minutes=10), attempts=0, created_at=now,
    ))
    db_session.commit()

    found = db_session.query(SignupVerification).filter_by(
        target="reset-target@example.com", purpose="password_reset"
    ).one()
    assert found.purpose == "password_reset"
```

- [ ] **Step 8: 테스트 실행**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k password_reset_purpose`
Expected: PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 10: 커밋**

```bash
git add schema.sql backend/app/models.py backend/app/email_verification.py \
  backend/tests/test_email_verification.py backend/tests/test_auth.py
git commit -m "feat: signup_verifications가 password_reset purpose를 지원하도록 확장"
```

---

### Task 2: 백엔드 — `/auth/password-reset/*` 엔드포인트

**Files:**
- Modify: `backend/app/routers/auth.py`
- Test: `backend/tests/test_auth.py`(파일 끝에 추가)

**Interfaces:**
- Consumes: Task 1의 `send_verification_email(to, code, purpose="signup")`. 기존 `_issue_code`, `_check_code`, `EMAIL_CODE_TTL`, `hash_password`.
- Produces: `POST /auth/password-reset/request` `{email}` → `{"sent": true}`. `POST /auth/password-reset/confirm` `{email, code, new_password}` → `{"reset": true}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_auth.py` 파일 끝에 추가:

```python
def test_password_reset_request_rejects_unknown_email(client, platforms):
    res = client.post("/auth/password-reset/request", json={"email": "nosuchuser@test.com"})
    assert res.status_code == 404


def test_password_reset_request_rejects_kakao_only_account(client, platforms, monkeypatch):
    from app.kakao import KakaoUser
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "exchange_code_for_token", lambda code, redirect_uri: "kakao-token")
    monkeypatch.setattr(
        auth_router, "fetch_kakao_user",
        lambda access_token: KakaoUser(id="5005", nickname="카카오전용", email="kakaoonly2@test.com"),
    )
    client.post(
        "/auth/kakao/callback",
        json={"code": "auth-code", "redirect_uri": "http://localhost:3000/auth/kakao/callback"},
    )

    res = client.post("/auth/password-reset/request", json={"email": "kakaoonly2@test.com"})
    assert res.status_code == 400
    assert "카카오" in res.json()["detail"]


def test_password_reset_request_sends_code_with_password_reset_purpose(client, seeded_user, monkeypatch):
    from app.routers import auth as auth_router

    sent = {}

    def _fake_send(to, code, purpose="signup"):
        sent["to"] = to
        sent["code"] = code
        sent["purpose"] = purpose

    monkeypatch.setattr(auth_router, "generate_code", lambda: "654321")
    monkeypatch.setattr(auth_router, "send_verification_email", _fake_send)

    res = client.post("/auth/password-reset/request", json={"email": "demo@dris.kr"})
    assert res.status_code == 200
    assert sent == {"to": "demo@dris.kr", "code": "654321", "purpose": "password_reset"}


def test_password_reset_confirm_changes_password_and_allows_login(client, seeded_user, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "654321")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code, purpose="signup": None)

    client.post("/auth/password-reset/request", json={"email": "demo@dris.kr"})
    res = client.post("/auth/password-reset/confirm", json={
        "email": "demo@dris.kr", "code": "654321", "new_password": "newpass1234!",
    })
    assert res.status_code == 200
    assert res.json() == {"reset": True}

    old_login = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert old_login.status_code == 401

    new_login = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "newpass1234!"})
    assert new_login.status_code == 200


def test_password_reset_confirm_rejects_wrong_code(client, seeded_user, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "654321")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code, purpose="signup": None)

    client.post("/auth/password-reset/request", json={"email": "demo@dris.kr"})
    res = client.post("/auth/password-reset/confirm", json={
        "email": "demo@dris.kr", "code": "000000", "new_password": "newpass1234!",
    })
    assert res.status_code == 400

    still_old = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert still_old.status_code == 200


def test_password_reset_confirm_consumes_verification_row(client, seeded_user, db_session, monkeypatch):
    from app.models import SignupVerification
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "654321")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code, purpose="signup": None)

    client.post("/auth/password-reset/request", json={"email": "demo@dris.kr"})
    client.post("/auth/password-reset/confirm", json={
        "email": "demo@dris.kr", "code": "654321", "new_password": "newpass1234!",
    })

    remaining = db_session.query(SignupVerification).filter_by(
        target="demo@dris.kr", purpose="password_reset"
    ).count()
    assert remaining == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k password_reset`
Expected: FAIL — `404 Not Found` (엔드포인트 없음)

- [ ] **Step 3: `backend/app/routers/auth.py`에 Pydantic 모델 2개 추가**

`backend/app/routers/auth.py:50`(`class LoginRequest` 다음 줄, `class KakaoCallbackRequest` 앞)에 삽입:

```python


class PasswordResetRequestBody(BaseModel):
    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    email: EmailStr
    code: str
    new_password: str
```

- [ ] **Step 4: 엔드포인트 2개 추가**

`backend/app/routers/auth.py:196`(`return TokenResponse(...)`로 끝나는 `signup()` 함수 다음, `@router.post("/login")` 앞)에 삽입:

```python


@router.post("/password-reset/request")
def request_password_reset(body: PasswordResetRequestBody, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise HTTPException(404, "가입된 계정이 없습니다")
    if user.password_hash is None:
        raise HTTPException(400, "카카오로 가입된 계정입니다. 카카오 로그인을 이용해주세요")

    code = _issue_code(db, body.email, "password_reset", EMAIL_CODE_TTL)
    try:
        send_verification_email(body.email, code, purpose="password_reset")
    except EmailSendError as e:
        logger.warning("비밀번호 재설정 이메일 발송 실패 (%s): %s", body.email, e)
        raise HTTPException(502, "이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요")
    return {"sent": True}


@router.post("/password-reset/confirm")
def confirm_password_reset(body: PasswordResetConfirmBody, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise HTTPException(404, "가입된 계정이 없습니다")

    _check_code(db, body.email, "password_reset", body.code)

    user.password_hash = hash_password(body.new_password)
    db.execute(delete(SignupVerification).where(
        SignupVerification.target == body.email, SignupVerification.purpose == "password_reset"
    ))
    db.commit()
    return {"reset": True}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v -k password_reset`
Expected: 5개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/auth.py backend/tests/test_auth.py
git commit -m "feat: POST /auth/password-reset/request, /confirm 엔드포인트 추가"
```

---

### Task 3: 프론트엔드 — 비밀번호 찾기 페이지 + 로그인 화면 연결

**Files:**
- Modify: `frontend/src/app/login/page.tsx` (전체 재작성 — `Suspense` 경계 추가)
- Create: `frontend/src/app/password-reset/page.tsx`

**Interfaces:**
- Consumes: Task 2의 `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm`. 기존 `apiPost`, `ApiError`(`@/lib/api`).
- Produces: 없음(최종 UI 계층).

- [ ] **Step 1: `frontend/src/app/login/page.tsx` 전체를 아래 내용으로 교체**

```tsx
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ApiError, apiPost, getToken, setToken } from "@/lib/api";
import { kakaoAuthorizeUrl } from "@/lib/kakao";

type TokenResponse = { access_token: string };

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (typeof window !== "undefined" && getToken()) {
    router.replace("/dashboard");
  }

  const resetSuccess = searchParams.get("reset") === "success";

  const submit = async (e: React.FormEvent, overrideEmail?: string, overridePassword?: string) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<TokenResponse>("/auth/login", {
        email: overrideEmail ?? email,
        password: overridePassword ?? password,
      });
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "로그인에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-8 flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-lg font-bold text-white">
            D
          </div>
          <div>
            <p className="text-base font-semibold">Delivery Review</p>
            <p className="text-xs text-muted">& Store Insight</p>
          </div>
        </div>

        {resetSuccess && (
          <p className="mb-4 rounded-lg border border-accent/40 bg-accent-soft px-3 py-2 text-xs text-accent">
            비밀번호가 재설정되었습니다. 새 비밀번호로 로그인해주세요.
          </p>
        )}

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-muted">이메일</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="you@store.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">비밀번호</label>
            <input
              type="password"
              required
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

        <p className="mt-3 text-center text-xs">
          <Link href="/password-reset" className="text-muted hover:text-foreground hover:underline">
            비밀번호를 잊으셨나요?
          </Link>
        </p>

        <button
          onClick={(e) => submit(e, "demo@dris.kr", "demo1234!")}
          disabled={loading}
          className="mt-3 w-full rounded-lg border border-border-subtle py-2.5 text-sm text-muted transition hover:border-accent hover:text-foreground disabled:opacity-50"
        >
          데모 계정으로 로그인
        </button>

        <button
          type="button"
          onClick={() => {
            try {
              window.location.href = kakaoAuthorizeUrl();
            } catch (err) {
              setError(err instanceof Error ? err.message : "카카오 로그인을 시작할 수 없습니다");
            }
          }}
          className="mt-3 w-full rounded-lg bg-[#FEE500] py-2.5 text-sm font-medium text-black transition hover:opacity-90"
        >
          카카오로 로그인
        </button>

        <p className="mt-6 text-center text-xs text-muted">
          계정이 없나요?{" "}
          <Link href="/signup" className="text-accent hover:underline">
            회원가입
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
```

(`useSearchParams`는 빌드 시 `Suspense` 경계 없이 쓰면 에러가 난다 — `frontend/src/app/auth/kakao/callback/page.tsx`에서 이미 쓴 것과 같은 패턴으로 내부 컴포넌트를 분리했다.)

- [ ] **Step 2: `frontend/src/app/password-reset/page.tsx` 신규 생성**

```tsx
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiPost } from "@/lib/api";

type Step = "email" | "reset";

export default function PasswordResetPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const sendCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/password-reset/request", { email });
      setCode("");
      setStep("reset");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발송에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword !== newPasswordConfirm) {
      setError("비밀번호가 일치하지 않습니다");
      return;
    }
    setLoading(true);
    try {
      await apiPost("/auth/password-reset/confirm", { email, code, new_password: newPassword });
      router.push("/login?reset=success");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "비밀번호 재설정에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6">
          <p className="text-base font-semibold">비밀번호 찾기</p>
          <p className="text-xs text-muted">가입한 이메일로 인증번호를 보내드립니다</p>
        </div>

        {error && <p className="mb-4 text-xs text-danger">{error}</p>}

        {step === "email" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">이메일</label>
              <input
                type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="you@store.com"
              />
            </div>
            <button
              type="button" disabled={loading || !email}
              onClick={sendCode}
              className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "발송 중..." : "인증번호 받기"}
            </button>
          </div>
        )}

        {step === "reset" && (
          <form onSubmit={submit} className="space-y-4">
            <p className="text-xs text-muted">{email}(으)로 인증번호를 보냈습니다.</p>
            <div>
              <label className="mb-1 block text-xs text-muted">인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={code} onChange={(e) => setCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">새 비밀번호</label>
              <input
                type="password" required minLength={8}
                value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="8자 이상"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">새 비밀번호 확인</label>
              <input
                type="password" required minLength={8}
                value={newPasswordConfirm} onChange={(e) => setNewPasswordConfirm(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="비밀번호를 한 번 더 입력해주세요"
              />
            </div>
            <div className="flex gap-2">
              <button
                type="button" onClick={() => setStep("email")}
                className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2"
              >
                뒤로
              </button>
              <button
                type="submit" disabled={loading}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "변경 중..." : "비밀번호 재설정"}
              </button>
            </div>
          </form>
        )}

        <p className="mt-6 text-center text-xs text-muted">
          <Link href="/login" className="text-accent hover:underline">
            로그인으로 돌아가기
          </Link>
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: 타입체크**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 에러 없음

(이 환경에서 `npx tsc`가 `rtk` 셸 훅에 가로채져 가짜 결과를 낼 수 있다는 게 이전 작업에서 확인됐다 — `node_modules/.bin/tsc`를 직접 호출해야 신뢰할 수 있는 결과가 나온다.)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/app/login/page.tsx frontend/src/app/password-reset/page.tsx
git commit -m "feat: 비밀번호 찾기 페이지 + 로그인 화면 연결"
```

---

### Task 4: 로컬 검증 + 배포 안내

**Files:** 없음(실행/검증만).

**Interfaces:** 없음(최종 검증 단계).

- [ ] **Step 1: 로컬 DB에 새 스키마 적용**

```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql
```
Expected: 에러 없이 완료(로컬 데이터는 초기화된다). 5432 포트가 이미 다른 컨테이너에 점유돼 있으면(흔한 상황), 임시로 다른 포트에 컨테이너를 띄우고 `DATABASE_URL`을 맞춰서 검증해도 무방하다.

- [ ] **Step 2: 백엔드 전체 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 3: 로컬 서버 기동 (실제 Resend API 키 필요)**

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

- [ ] **Step 4: 브라우저로 실제 비밀번호 재설정 흐름 확인**

`http://localhost:3000/login` → "비밀번호를 잊으셨나요?" 클릭 → **Resend 계정 소유자 본인 이메일**(도메인 미인증 상태라 타인 이메일은 실제 수신이 안 될 수 있음)로 인증번호 요청 → 실제 수신함에서 코드 확인 → 새 비밀번호 입력 → 재설정 → `/login?reset=success`로 이동해 성공 안내가 보이는지 확인 → 새 비밀번호로 실제 로그인까지 확인한다.

- [ ] **Step 5: 커밋 (필요 시)**

이 태스크는 검증 단계라 보통 코드 변경이 없다. 검증 중 버그를 발견해 수정했다면 그 수정을 별도로 커밋한다.

- [ ] **Step 6: 배포 안내 (실행은 사용자 확인 후)**

로컬 검증이 끝나면 Railway 배포가 남는다. 프로덕션(공유 상태)을 바꾸는 작업이라 실행 전 반드시 사용자에게 확인받는다:

1. 프로덕션 Postgres에서 먼저 현재 제약 이름을 확인한다:
   ```sql
   \d signup_verifications
   ```
   `purpose` 컬럼에 걸린 CHECK 제약 이름(보통 Postgres 기본 명명 규칙상
   `signup_verifications_purpose_check`)을 확인한 뒤, 아래 증분 SQL을 그
   이름에 맞춰 실행한다 — **`schema.sql` 전체를 재실행하지 않는다** (`DROP
   TABLE ... CASCADE`로 기존 데이터가 전부 날아간다):
   ```sql
   ALTER TABLE signup_verifications ALTER COLUMN purpose TYPE VARCHAR(20);
   ALTER TABLE signup_verifications DROP CONSTRAINT signup_verifications_purpose_check;
   ALTER TABLE signup_verifications ADD CONSTRAINT signup_verifications_purpose_check
       CHECK (purpose IN ('email', 'phone', 'password_reset'));
   ```
   실행 전 반드시 사용자에게 다시 확인받는다.
2. Railway `backend`/`frontend` 서비스는 이미 `RESEND_API_KEY`,
   `EMAIL_FROM_ADDRESS`가 설정돼 있으므로(이메일 인증 기능 배포 때 완료)
   추가 환경변수는 필요 없다.
3. `backend`, `frontend` 두 서비스를 재배포한다. Railway MCP의 `deploy`
   도구에 `path`를 `backend/`, `frontend/`로 각각 명시해서 호출하는 방식이
   이전 세션에서 안정적으로 동작했다. 배포 후 `list_deployments`로 상태가
   `SUCCESS`가 될 때까지 반드시 확인한다.

## 다음 단계 (이번 계획 범위 밖)

- 로그인 상태에서의 비밀번호 변경(계정 설정 화면) — 이번은 "잊어버렸을 때" 복구 흐름만.
- 결제/구독 → 실플랫폼 연동 → LLM+RAG 답글 (`CLAUDE.md`의 "방향 전환" 절 순서대로).
