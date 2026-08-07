# 비밀번호 찾기 — 설계

날짜: 2026-08-07
관련 결정: `docs/superpowers/specs/2026-08-07-email-signup-verification-design.md`에서
구축한 이메일 인증 인프라(Resend 실발송, `signup_verifications` 테이블,
`_issue_code`/`_check_code`)를 재사용한다.

## 배경 / 목적

로그인 화면에 이메일/비밀번호만 있고 비밀번호를 잊었을 때 복구할 방법이 없다.
"아이디 찾기"는 로그인 자체가 이메일 기반이라 별도 기능으로 의미가 없어
범위에서 뺀다(사용자 확인 완료). 비밀번호 찾기만 구현한다.

## 대상

- 이메일 기반 로그인 계정(`password_hash IS NOT NULL`)만 대상.
- 카카오 전용 계정(`password_hash IS NULL`)은 애초에 비밀번호가 없으므로
  대상이 아니다 — 요청 시 "카카오로 가입된 계정입니다. 카카오 로그인을
  이용해주세요"로 명확히 안내한다.
- 가입되지 않은 이메일도 "가입된 계정이 없습니다"로 명확히 안내한다. 이
  프로젝트는 회원가입의 이메일 중복 체크(`POST /auth/signup/email-code`가
  이미 가입 여부를 그대로 노출)에서 보듯 이메일 열거(enumeration) 공격을
  방어하는 정책을 쓰지 않는다 — 포트폴리오 데모 규모에서 사용자 친화성이
  우선이라는 기존 결정과 일관되게 간다.

## 흐름

로그인 화면에 "비밀번호를 잊으셨나요?" 링크 → `/password-reset` 새 페이지.

1. **이메일 입력** 단계: 이메일을 입력하고 "인증번호 받기" → 코드 발송.
2. **인증번호 + 새 비밀번호** 단계: 회원가입 위자드와 달리 코드 확인을 별도
   화면으로 쪼개지 않는다 — 인증번호, 새 비밀번호, 새 비밀번호 확인을 **한
   화면에서 같이** 입력받아 한 번에 제출한다(필드가 적어 굳이 나눌 필요가
   없다).
3. 성공하면 **자동 로그인하지 않고** 로그인 화면으로 돌려보낸다 — 재설정
   직후 새 비밀번호를 실제로 아는지 한 번 더 확인시키는 일반적인 보안
   관행을 따른다. 로그인 화면에 짧은 성공 안내를 보여준다.

## 데이터 모델

새 테이블을 만들지 않는다. `signup_verifications`의 `purpose` CHECK 제약을
`'email'`, `'phone'`, `'password_reset'` 세 값으로 확장한다. `target`은
이메일 재설정이므로 이메일 주소 평문(회원가입의 이메일 코드와 동일한
관례). `_issue_code`/`_check_code`/만료·쿨다운·시도횟수 로직은 이미
`purpose`로 분리돼 있어 변경 없이 그대로 재사용한다.

테이블 이름이 "signup_verifications"라 가입 전용처럼 보이지만, 이미
회원가입 목적으로 커밋되어 있고 이름을 바꾸려면 스키마·모델·라우터·테스트·
운영 마이그레이션을 전부 건드려야 해서 이번 기능만으로는 배보다 배꼽이
크다. 이름은 그대로 두고, 스키마 주석에 "가입 이메일 인증 + 비밀번호
재설정 코드에 공용으로 쓰인다"는 점만 명시한다.

## 백엔드

### `backend/app/email_verification.py` 확장

`send_verification_email(to, code)`가 "이메일 인증번호"라는 회원가입 전용
제목/본문을 하드코딩하고 있어 비밀번호 재설정에 그대로 쓰면 사용자가
헷갈릴 수 있다. `purpose` 파라미터를 추가해 제목/본문을 상황에 맞게
분기한다: 회원가입은 기존 문구 그대로, 비밀번호 재설정은 "비밀번호 재설정
인증번호"로 표시.

### `backend/app/routers/auth.py`에 엔드포인트 2개 추가

- `POST /auth/password-reset/request` `{email}`
  - 계정 없음 → 404 "가입된 계정이 없습니다"
  - 카카오 전용(`password_hash IS NULL`) → 400 "카카오로 가입된 계정입니다.
    카카오 로그인을 이용해주세요"
  - `_issue_code(db, email, "password_reset", EMAIL_CODE_TTL)` 재사용,
    같은 60초 쿨다운/발송 실패 502 처리를 회원가입 이메일 코드 엔드포인트와
    동일하게 적용
  - 응답: `{"sent": true}`
- `POST /auth/password-reset/confirm` `{email, code, new_password}`
  - `_check_code(db, email, "password_reset", code)`로 재검증(코드
    만료/불일치/시도초과는 기존과 동일한 메시지·attempts 카운트 로직 그대로)
  - 통과 시 `user.password_hash = hash_password(new_password)`,
    `signup_verifications`에서 사용된 행 삭제
  - 응답: `{"reset": true}` (토큰 발급 없음 — 자동 로그인하지 않는다는
    설계 결정)

## 프론트엔드

- `frontend/src/app/login/page.tsx`: 비밀번호 입력 필드 아래에 "비밀번호를
  잊으셨나요?" 링크(`/password-reset`)를 추가한다.
- `frontend/src/app/password-reset/page.tsx` 신규: 2단계 로컬 state 기반
  페이지(회원가입 위자드와 같은 패턴, 진행 표시는 2단계라 생략하거나 간단한
  두 점으로). 1단계: 이메일 입력 + 발송. 2단계: 인증번호 + 새 비밀번호 +
  새 비밀번호 확인(클라이언트에서 일치 검증) + "비밀번호 재설정" 제출 →
  성공 시 `/login?reset=success`로 이동.
- `frontend/src/app/login/page.tsx`가 `?reset=success` 쿼리를 읽어 짧은
  성공 안내 문구를 보여준다(별도 상태 관리 없이 `useSearchParams`로 1회
  확인). `useSearchParams`를 쓰므로 카카오 콜백 페이지 때와 동일하게
  `Suspense` 경계가 필요하다.

## 에러 처리 요약

| 상황 | 처리 |
|---|---|
| 가입되지 않은 이메일 | 404 "가입된 계정이 없습니다" |
| 카카오 전용 계정 | 400 "카카오로 가입된 계정입니다. 카카오 로그인을 이용해주세요" |
| 재전송 쿨다운 중 | 429 (회원가입과 동일) |
| 코드 만료 | 400 "인증번호가 만료되었습니다. 다시 받아주세요" |
| 코드 불일치 | 400 "인증번호가 올바르지 않습니다" (시도 횟수 카운트) |
| 시도 5회 초과 | 400, 재발급 필요 |
| 새 비밀번호 확인 불일치 | 클라이언트에서만 검증(회원가입과 동일 — `password_confirm`은 API로 보내지 않음) |

## 테스트 계획

- **backend (pytest)**: `password-reset/request`가 존재하지 않는 이메일/
  카카오 전용 계정/정상 케이스를 올바르게 분기하는지, `confirm`이 코드
  재검증 후 `password_hash`를 실제로 교체하고(로그인 테스트로 확인) 인증
  행을 삭제하는지, 쿨다운·만료·시도초과가 기존 로직 재사용으로 그대로
  동작하는지. `signup_flow`와 같은 패턴으로 `generate_code`/
  `send_verification_email`을 monkeypatch.
- **frontend**: `tsc --noEmit` 통과. 로컬 브라우저로 전체 플로우(본인
  이메일로 실제 코드 수신 1회 확인 포함) 직접 클릭 테스트.

## 배포 시 운영 DB 반영

`schema.sql` 전체 재실행 금지(기존 데이터 삭제 위험). 증분 SQL 한 문장만
실행:

```sql
ALTER TABLE signup_verifications DROP CONSTRAINT signup_verifications_purpose_check;
ALTER TABLE signup_verifications ADD CONSTRAINT signup_verifications_purpose_check
    CHECK (purpose IN ('email', 'phone', 'password_reset'));
```

(제약 이름은 실제 운영 DB에 생성된 이름을 `\d signup_verifications`로 먼저
확인 후 맞춘다 — Postgres가 자동 생성한 이름이 다를 수 있다.)

## 범위 밖

- 아이디 찾기 (사용자 확인 — 이메일 기반 로그인이라 불필요).
- 비밀번호 재설정 후 자동 로그인 (의도적으로 뺌 — 위 "흐름" 절 참고).
- 로그인 상태에서의 비밀번호 변경(기존 계정 설정 화면에 있다면 별개 기능,
  이번 범위는 "잊어버렸을 때" 복구 흐름만).
