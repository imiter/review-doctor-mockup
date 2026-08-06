# 이메일 회원가입 다단계 인증 — 설계

날짜: 2026-08-07
관련 결정: CLAUDE.md "방향 전환 (2026-08-06, 실 SaaS 확장 결정)" — 카카오 로그인에 이어
이메일 회원가입도 실제로 동작하는 인증 흐름으로 개편한다.

## 배경 / 목적

현재 `/auth/signup`은 닉네임/이메일/비밀번호/휴대폰(선택)을 한 번에 입력받아 즉시 계정을
생성한다. 이메일 실소유 확인이나 휴대폰 확인이 전혀 없다 — 실 SaaS 포트폴리오 데모로서는
허술하다. 이번 작업은 이 흐름을 실제 서비스처럼 다단계 인증 위자드로 바꾼다:

**닉네임/이메일 입력 → 이메일 인증 → 휴대폰 번호 입력 → 휴대폰 인증(Mock) → 비밀번호+확인
→ 가입 완료(로그인)**

카카오 소셜 로그인 플로우는 이 작업 범위 밖이며 변경하지 않는다.

## 제약 조건

- CLAUDE.md의 "교육 과정 시절 원칙"에 따라 **실제 문자(SMS)/카카오톡 발송은 여전히
  금지**다. 휴대폰 인증은 Mock으로만 구현한다 — 서버가 인증번호를 생성해 API 응답에
  그대로 반환하고, 프론트엔드가 화면에 표시한다 (실제 발송 없음).
- 이메일 인증은 금지 목록에 없으므로 이번 기회에 실제로 발송한다. 발송 서비스는
  **Resend**를 사용한다 (REST API, `httpx`로 직접 호출 — 별도 SDK 의존성 추가 없음,
  기존 `kakao.py`와 동일한 패턴).
- Resend는 커스텀 도메인을 인증하기 전까지는 기본 발신 주소
  (`onboarding@resend.dev`)로 **Resend 계정 소유자 본인 이메일 외에는 실제 수신이
  제한될 수 있다.** 이번 작업은 우선 기본 도메인으로 시작한다 — 타인 이메일로 가입
  테스트 시 실제 수신이 안 될 수 있다는 점은 알려진 제약으로 남겨두고, 추후 커스텀
  도메인을 인증하면 자연히 해소된다.
- `users` 개인정보 원칙 유지: 전화번호는 원문 대신 `phone_hash`로만 저장한다. 이번
  기능의 휴대폰 인증 코드 테이블에도 원문 전화번호를 저장하지 않는다.

## 데이터 모델

새 테이블 `signup_verifications` 하나만 추가한다. `users` 테이블은 변경하지 않는다 —
계정은 항상 이메일·휴대폰 인증이 모두 끝난 뒤에만 생성되므로, "미인증 계정"이라는
상태 자체가 존재하지 않는다. 따라서 운영 DB에 컬럼을 추가하는 마이그레이션이
필요 없다 (schema.sql에 테이블만 추가하고, 운영 DB에는 `CREATE TABLE`
한 문장만 증분 실행하면 된다).

```sql
CREATE TABLE signup_verifications (
    id BIGSERIAL PRIMARY KEY,
    target VARCHAR(255) NOT NULL,       -- email: 평문 이메일 / phone: sha256 해시
    purpose VARCHAR(10) NOT NULL CHECK (purpose IN ('email', 'phone')),
    code_hash VARCHAR(64) NOT NULL,     -- 6자리 코드의 sha256
    expires_at TIMESTAMPTZ NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_signup_verifications_target ON signup_verifications (target, purpose);
```

- 동일 `(target, purpose)`로 재발송 요청이 오면 기존 행을 갱신(upsert)한다 — 이전
  코드는 자동으로 무효화된다.
- 코드는 6자리 숫자, 이메일은 10분, 휴대폰(Mock)은 5분 만료.
- 재발송 쿨다운 60초 (직전 코드의 `created_at` 기준).
- 시도 5회 초과 시 해당 코드는 더 이상 유효하지 않음 — 재발급(재발송) 필요.
- 계정 생성이 최종 완료되면 사용된 두 행은 삭제한다 (정리).
- 중간에 이탈해 만료된 행은 그냥 방치해도 무해하다 (다음 재발송 시 덮어써지고,
  `expires_at` 지난 행은 어차피 검증에서 실패 처리되므로 별도 배치 정리는
  이번 범위에서 만들지 않는다 — YAGNI).

## 백엔드

### 새 모듈: `backend/app/email_verification.py`

`kakao.py`와 같은 패턴 — 외부 연동을 라우터에서 분리한 얇은 모듈.

- `generate_code() -> str`: 6자리 숫자 코드 생성 (`secrets.randbelow`)
- `hash_code(code: str) -> str`: sha256
- `send_verification_email(to: str, code: str) -> None`: Resend REST API
  (`POST https://api.resend.com/emails`) 호출. 실패 시 `EmailSendError` 예외.
- 휴대폰 코드는 별도 발송 함수가 없다 (Mock이므로 생성만 하고 응답에 그대로 반환).

### 라우터: `backend/app/routers/auth.py`에 엔드포인트 추가

- `POST /auth/signup/email-code` `{email}`
  - 이미 가입된 이메일이면 409
  - 60초 이내 재요청이면 429
  - 코드 생성 → `signup_verifications` upsert → Resend 발송 → 발송 실패 시 502
    (사용자에게는 일반 메시지, 서버 로그에 상세 원인)
  - 응답: `{"sent": true}`
- `POST /auth/signup/verify-email-code` `{email, code}`
  - 행 없음/만료: 400 "인증번호가 만료되었습니다"
  - 시도 5회 초과: 400 "시도 횟수를 초과했습니다. 인증번호를 다시 받아주세요"
  - 불일치: attempts += 1, 400 "인증번호가 올바르지 않습니다"
  - 일치: 200 (행은 소비하지 않음 — 최종 제출에서 다시 검증)
- `POST /auth/signup/phone-code` `{phone}`
  - 휴대폰 형식 검증, sha256 해시로 target 계산
  - 60초 쿨다운은 이메일과 동일
  - 코드 생성 → upsert → **응답에 코드 그대로 포함**: `{"mock_code": "482913"}`
- `POST /auth/signup/verify-phone-code` `{phone, code}` — 이메일 쪽과 동일 로직
- `POST /auth/signup` `{nickname, email, email_code, phone, phone_code, password,
  marketing_agreed}` (기존 엔드포인트 확장, `password_confirm`은 프론트에서만
  검사하고 서버로는 보내지 않는다)
  - 이메일 코드 재검증 (없음/만료/불일치 → 400)
  - 휴대폰 코드 재검증 (없음/만료/불일치 → 400)
  - 이메일 중복 재확인 (기존 409 로직 유지 — 동시 가입 레이스 대비)
  - `User` 생성 (`phone_hash` 필수로 전환), 사용된 `signup_verifications` 2행 삭제
  - `_create_default_store_and_subscription` 호출 (기존과 동일)
  - `TokenResponse` 반환 (기존과 동일)

휴대폰이 선택에서 필수로 바뀌므로 `SignupRequest`의 `phone: str | None = None`을
`phone: str`로, `phone_hash` 계산도 무조건 수행하도록 변경한다.

## 프론트엔드

`frontend/src/app/signup/page.tsx`를 5단계 위자드로 재작성한다. 단계는 컴포넌트
내부 state(`step: "email" | "email-code" | "phone" | "phone-code" | "password"`)로
관리하고, 라우트는 그대로 `/signup` 하나를 유지한다 (기존 프로젝트 패턴 — 별도
모달/스텝 URL을 쓰지 않고 클라이언트 state로 처리하는 방식과 일관).

1. **이메일 단계**: 닉네임 + 이메일 입력, "인증번호 받기" → `POST
   /auth/signup/email-code` → 성공 시 다음 단계로, 안내 문구 표시
2. **이메일 코드 단계**: 6자리 입력 + "확인" → `POST /auth/signup/verify-email-code`
   → 성공 시 다음 단계. "재전송" 버튼 (60초 카운트다운). "뒤로" 로 이메일 단계로
   복귀 가능 (재입력 시 새로 발송)
3. **휴대폰 단계**: 휴대폰 번호 입력, "인증번호 받기" → `POST
   /auth/signup/phone-code` → 응답의 `mock_code`를 배너로 표시: "데모용
   인증번호: 482913 (Mock — 실제 문자는 발송되지 않습니다)". 자동으로 입력창에
   채우지 않는다 — 사용자가 직접 타이핑해서 실제 인증 흐름을 그대로 체험하게 한다
4. **휴대폰 코드 단계**: 6자리 입력 + "확인" → `POST /auth/signup/verify-phone-code`
5. **비밀번호 단계**: 비밀번호 + 비밀번호 확인 입력. 클라이언트에서 일치 여부
   검증 후 "가입 완료" → `POST /auth/signup` (email_code/phone_code 포함) →
   성공 시 기존과 동일하게 `setToken` + `/dashboard` 이동

각 단계 상단에 진행 표시(1/5 ~ 5/5 점 또는 바)를 둔다. 기존 다크 테마·폼 스타일
컴포넌트를 그대로 재사용한다 (신규 UI 컴포넌트 라이브러리 도입 없음).

## 에러 처리 요약

| 상황 | 처리 |
|---|---|
| 이미 가입된 이메일 | 1단계에서 즉시 안내 + 로그인 링크 |
| 이메일 발송 실패 (Resend 오류) | 일반 에러 메시지, 서버 로그에 상세 원인 |
| 코드 만료 | "인증번호가 만료되었습니다" + 재전송 강조 |
| 코드 불일치 | "인증번호가 올바르지 않습니다 (n/5)" |
| 시도 5회 초과 | 재발급 요구, 기존 코드 무효 |
| 재전송 쿨다운 중 | 버튼 비활성 + 남은 초 표시 |
| 최종 제출 시점에 코드 만료 (오래 붙잡고 있다가 제출) | 400 반환, 해당 단계로
  안내하며 되돌림 |

## 테스트 계획

- **backend (pytest)**: 코드 생성/해시/만료/시도초과/재발송쿨다운 단위 테스트,
  `/auth/signup` 최종 제출이 두 코드를 재검증하는지, 이메일 중복/휴대폰 필수화
  회귀 테스트. Resend 호출은 `test_kakao.py`의 `_FakeResponse` monkeypatch 패턴을
  재사용해 실제 네트워크 호출 없이 검증.
- **frontend**: `tsc --noEmit` 통과. 로컬 브라우저로 5단계 전체를 직접 클릭
  테스트 (이메일은 본인 계정으로 실제 수신 1회 확인, 휴대폰은 Mock 배너 확인).

## CLAUDE.md 갱신

기존 "카카오 소셜 로그인 (예외 허용)" 절과 같은 형식으로 "이메일 인증 (실제 발송,
예외 허용)" 절을 추가한다. 휴대폰 인증은 여전히 Mock이며 실제 SMS 발송 금지
원칙은 그대로 유지됨을 명시한다. "추가 합의 사항"의 로그인 관련 서술도 갱신한다.

## 환경 변수

`backend/.env.example`에 추가:
- `RESEND_API_KEY` — Resend 발급 API 키
- `EMAIL_FROM_ADDRESS` — 기본값 `onboarding@resend.dev` (커스텀 도메인 인증 후
  전환 가능)

## 범위 밖

- 커스텀 도메인 인증 (추후 별도 작업)
- 비밀번호 재설정/이메일 변경 시 재인증 플로우 (이번 작업은 신규 가입만 다룸)
- 카카오 로그인 흐름 (변경 없음)
- 미인증 상태로 남은 `signup_verifications` 행에 대한 배치 정리 (TTL로 자연 방치)
