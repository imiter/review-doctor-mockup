# 카카오 소셜 로그인 — 설계서

- 날짜: 2026-08-06
- 작성 배경: 이 프로젝트를 Mock 기반 교육 과제물에서 실제 SaaS(외주 포트폴리오
  데모용)로 단계적으로 전환하기로 결정했다(`CLAUDE.md`의 "방향 전환" 절 참고).
  그 첫 단계로 이메일 로그인에 카카오 소셜 로그인을 병행 추가한다.
- 카카오 비즈니스 앱 전환(사업자 등록 연동 + 카카오 심사)은 이번 범위 밖이다.
  그래서 카카오 로그인에서 이메일 동의 항목을 받을 수 없다 — 카카오 고유
  회원번호와 닉네임만으로 로그인/가입이 되게 만든다.

## 목적

기존 이메일/비밀번호 로그인은 그대로 두고, "카카오로 로그인" 버튼 하나를
추가해 실제 OAuth 플로우로 로그인/가입이 되게 한다.

## 범위와 원칙

- 카카오만 지원한다. 네이버/구글/애플은 범위 밖(향후 같은 패턴으로 추가 가능한
  구조로만 만들어둔다).
- 카카오 로그인은 이메일 동의 항목 없이 진행한다(비즈니스 미인증 상태이므로
  기본 제공 항목인 카카오 고유 회원번호 + 닉네임만 사용).
- 이메일 회원가입/로그인 API(`/auth/signup`, `/auth/login`)는 변경하지 않고
  그대로 유지한다.
- 계정 자동 연결: 카카오가 이메일을 준 경우(향후 비즈니스 인증 후) 기존 이메일
  계정과 이메일이 일치하면 자동으로 연결한다. 현재는 이메일을 못 받으므로
  실질적으로는 항상 신규 계정이 생성되지만, 로직은 나중을 대비해 이메일 매칭
  분기를 포함해둔다.
- 카카오로 신규 가입된 사용자도 기존 이메일 가입과 동일하게 기본 매장 1개 +
  배민 연결 + Basic 구독을 자동 생성한다(빈 대시보드 방지).
- 복잡한 권한/다중 사업자 권한 관리는 여전히 금지 — 사장 1명 = 로그인 1개
  기준 그대로 유지.

## 플로우

1. 프론트 로그인 페이지의 "카카오로 로그인" 버튼 클릭 → 카카오 인가 URL
   (`https://kauth.kakao.com/oauth/authorize?client_id=...&redirect_uri=...&response_type=code`)로 이동.
2. 사용자 동의 후 카카오가 프론트의 `/auth/kakao/callback?code=...`로 리다이렉트.
3. 프론트가 `code`를 백엔드 `POST /auth/kakao/callback`에 전달한다. 카카오
   REST API 키/시크릿은 백엔드 환경변수에만 두고 프론트에는 절대 노출하지
   않는다.
4. 백엔드가 `https://kauth.kakao.com/oauth/token`으로 code를 access_token으로
   교환하고, `https://kapi.kakao.com/v2/user/me`로 카카오 고유 회원번호 +
   `properties.nickname`(+ 있다면 이메일)을 조회한다.
5. 계정 매칭 로직(아래) 수행 후 기존 `create_token()`으로 동일한 형태의 JWT
   발급, 기존 `TokenResponse`와 같은 응답 형식으로 반환.
6. 프론트는 기존 이메일 로그인과 동일하게 토큰을 저장하고 대시보드로 이동.

## 계정 매칭 로직

1. `social_accounts`에 `provider='kakao'` AND `provider_user_id=<카카오 회원번호>`가
   이미 있으면 그 `user_id`로 즉시 로그인.
2. 없고, 카카오가 이메일을 줬고, 그 이메일과 일치하는 `users.email`이 있으면
   그 계정에 `social_accounts` 행을 추가해 연결(자동 링크) 후 로그인.
3. 그 외에는 신규 `users` 생성(`email`=카카오가 준 이메일 또는 NULL,
   `password_hash`=NULL, `nickname`=카카오 닉네임) + 기본 매장/배민 연결/
   Basic 구독 생성(기존 `signup()`과 동일 패턴, 공용 헬퍼로 추출) + `social_accounts`
   행 추가 → 로그인.

## DB 스키마 변경

`schema.sql`에 반영, SQLAlchemy 모델(`models.py`)도 1:1로 맞춘다.

```sql
-- users 변경
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;  -- 카카오 전용 계정은 비밀번호 없음
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;          -- 이메일 미동의 카카오 계정 허용
DROP INDEX IF EXISTS users_email_key;                        -- 기존 UNIQUE 제약 대체
CREATE UNIQUE INDEX users_email_unique ON users(email) WHERE email IS NOT NULL;

-- 신규 테이블: 소셜 계정 연결 (platforms/store_platform_connections와 같은
-- "provider 문자열 기반 확장 가능한 패턴", 나중에 네이버/구글 추가해도
-- 이 테이블에 행만 늘면 됨 — 스키마 재변경 불필요)
CREATE TABLE social_accounts (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider          VARCHAR(20) NOT NULL,   -- kakao (향후 naver, google 등)
    provider_user_id  VARCHAR(100) NOT NULL,  -- 카카오 고유 회원번호
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_social_accounts_user ON social_accounts(user_id);
```

`seed.sql`은 변경하지 않는다(데모 계정은 계속 이메일 로그인으로 유지).

## 백엔드

- `backend/app/auth.py`: 변경 없음(`create_token`, `get_current_user` 그대로 재사용).
- `backend/app/routers/auth.py`:
  - `signup()` 안의 "기본 매장 + 배민 연결 + Basic 구독 생성" 블록을
    `_create_default_store_and_subscription(user, db)` 헬퍼로 추출해 카카오
    신규 가입 경로와 공유한다.
  - `POST /auth/kakao/callback` 신규 추가. 요청 바디 `{ "code": str }`,
    응답은 기존 `TokenResponse`와 동일.
  - 카카오 API 호출은 `backend/app/kakao.py` 신규 파일로 분리
    (`exchange_code_for_token`, `fetch_kakao_user`) — `acos.py`처럼 순수
    로직을 라우터에서 분리하는 기존 패턴을 따른다.
- 환경변수: `KAKAO_CLIENT_ID`(REST API 키), `KAKAO_REDIRECT_URI`
  (환경별로 다르므로 로컬/배포 각각 `.env`에 설정). 카카오 REST API 방식은
  client secret이 선택 항목이라, 콘솔에서 활성화한 경우에만
  `KAKAO_CLIENT_SECRET`도 함께 싣는다.

## 프론트엔드

- 로그인 페이지에 "카카오로 로그인" 버튼 추가(카카오 브랜드 가이드 색상
  `#FEE500` 배경 + 검정 텍스트만 적용, 나머지 다크 테마는 유지).
- `frontend/src/app/auth/kakao/callback/page.tsx` 신규: URL의 `code` 쿼리를
  읽어 백엔드 `POST /auth/kakao/callback` 호출 → 토큰 저장 → `/dashboard`로
  이동. 실패 시 로그인 페이지로 되돌리고 에러 메시지 표시.
- 카카오 인가 URL 리다이렉트는 버튼 클릭 시 `window.location.href`로 직접
  이동(별도 SDK 불필요 — REST API 방식이라 `kakao.js` SDK를 안 붙여도 됨).

## 에러 처리

- 카카오 토큰 교환/사용자 조회 실패 → `502` + "카카오 로그인에 실패했습니다"
  (프론트는 로그인 페이지로 되돌리고 배너 표시).
- `code` 재사용(만료/이미 사용됨) → 카카오가 400을 주는 그대로 502로 매핑.
- 이메일 UNIQUE 충돌 등 예기치 못한 DB 오류는 기존 `signup()`과 동일하게
  처리(트랜잭션 롤백 후 500).

## 테스트

- `backend/tests/test_auth.py`에 추가:
  - 신규 카카오 사용자 → `social_accounts` 생성 + 기본 매장/구독 생성 확인
    (카카오 API 호출은 `kakao.py`의 함수를 모킹).
  - 기존 `social_accounts` 매칭 → 같은 `user_id`로 로그인되는지 확인.
  - 이메일 일치 자동 연결 케이스(카카오가 이메일을 준다고 가정한 모킹으로).
- 프론트는 `tsc --noEmit`만 기준(기존 프로젝트 관례).
- 최종 검증은 로컬(`localhost:3000`)에서 실제 카카오 계정으로 로그인 버튼을
  눌러 카카오 동의 화면 → 콜백 → 대시보드까지 브라우저로 직접 확인한다.

## 산출물 (이번 스펙의 완료 기준)

- `schema.sql`, `models.py`에 `social_accounts` 테이블과 `users` nullable
  변경 반영.
- 로그인 페이지에서 "카카오로 로그인" 클릭 → 카카오 동의 → 콜백 → 대시보드
  진입까지 로컬에서 실제로 동작.
- 신규 카카오 계정도 기본 매장/구독이 자동 생성돼 빈 대시보드 없이 바로
  사용 가능.
- `backend/tests` 전체 통과.

## 다음 단계 (이번 스펙 범위 밖)

- 카카오 비즈니스 앱 인증 후 이메일 동의 항목 추가.
- 네이버/구글 등 추가 provider.
- 결제/구독, 실플랫폼 연동, LLM+RAG 답글 — `CLAUDE.md`의 "방향 전환" 절에
  적힌 다음 순서.
