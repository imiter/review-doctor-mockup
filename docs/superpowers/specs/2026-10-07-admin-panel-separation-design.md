# 관리자 패널 완전 분리 설계

## 배경 / 목적

관리자 패널(결제 이력/매장 운영 현황/유저 관리/LLMOps 4개 화면)은 지금
사장님이 쓰는 SaaS 프론트엔드(`frontend/`) 안에 `(admin)` 라우트 그룹
(`/ops-4k9x2m`)으로 들어있고, 로그인한 사장님 계정의 `users.role`이
`'admin'`일 때만 보이도록 백엔드(`require_admin`)와 프론트(레이아웃)
양쪽에서 막고 있다.

사용자가 "관리자 페이지를 SaaS에 들어가서 보게 하는 게 아니라 따로 나만
보게끔" 바꾸고 싶다고 요청했다 — 사장님 회원 체계(가입/로그인/`role`
컬럼)와 완전히 무관하게, 같은 사이트 안에는 admin 경로 자체가 없고, 전혀
다른 도메인의 별도 앱으로 분리하는 걸 의미한다. 세 가지 분리 수준(완전
별도 배포 / 같은 앱이지만 별도 로그인 / 현재 구조 유지+노출만 축소) 중
**완전 별도 배포**를, 저장소 구조는 **같은 레포에 `admin/` 폴더 추가**를,
인증 방식은 **단일 비밀번호**를 사용자가 직접 골랐다.

## 아키텍처

```
review-docter/
├── frontend/   (기존, 사장님 SaaS — admin 경로 전부 제거)
├── backend/    (기존 FastAPI, admin 전용 엔드포인트는 계속 여기 — 새 서비스 안 만듦)
└── admin/      (신규, 완전히 별도의 Next.js 앱)
```

- `admin/`은 `frontend/`와 같은 레벨의 독립 Next.js 프로젝트다(자체
  `package.json`/`tsconfig.json`/`next.config.ts`/`railway.json`). 이
  프로젝트의 기존 관례(`railway up <dir> --path-as-root --service <name>
  --environment production --detach`)를 그대로 따라 **새 Railway 서비스
  `admin`**으로 배포한다 — 기존 `frontend`/`backend` 서비스 배포 방식과
  동일하되 완전히 새 서비스라 별도 도메인(`admin-production-xxxx.up.
  railway.app` 형태)을 받는다.
- 백엔드는 **새 서비스를 만들지 않는다** — 기존 `backend` 서비스가 그대로
  admin 전용 엔드포인트(`admin.py`/`admin_llmops.py`)도 계속 서빙한다.
  admin 앱은 이 기존 backend 도메인을 그대로 호출한다. 분리되는 건
  "누가 어떤 도메인/인증으로 이 엔드포인트에 접근하느냐"이지 백엔드
  자체가 아니다.

## 인증

사장님 회원(`users` 테이블, JWT 기반 세션)과 완전히 독립된 별도 인증을
새로 만든다.

- 백엔드에 새 환경변수 `ADMIN_PASSWORD`(단일 비밀번호)와
  `ADMIN_JWT_SECRET`(이 admin 세션 전용 서명 키 — 사장님 로그인의
  `JWT_SECRET`과 섞이지 않도록 별도로 둔다)를 추가한다. 로컬 개발
  기본값은 기존 `JWT_SECRET`과 같은 패턴(미설정 시 개발용 고정 문자열로
  폴백, 운영에서는 반드시 채움)으로 `backend/.env.example`에 추가한다.
- 새 라우터 `backend/app/routers/admin_auth.py`: `POST /admin-auth/login`
  이 body의 `password`를 `ADMIN_PASSWORD`와 `hmac.compare_digest`로
  비교(타이밍 공격 방지)하고, 맞으면 `{"admin": true, "exp": ...}` 페이로드를
  `ADMIN_JWT_SECRET`으로 서명한 JWT를 돌려준다. 만료는 기존 사장님 세션과
  같은 7일(`JWT_EXPIRE_HOURS` 상수와 동일 값, admin 쪽에도 같은 상수를
  따로 둔다). 틀리면 401.
- 새 의존성 `require_admin_token`(위치는 `admin_auth.py`): `Authorization:
  Bearer` 토큰을 `ADMIN_JWT_SECRET`으로 디코드하고 `payload.get("admin")
  is True`인지만 확인한다 — DB 조회가 전혀 없다(admin이 User 레코드가
  아니므로).
- 기존 `app/auth.py`의 `require_admin`(User.role 체크)은 **삭제**한다 —
  대체하는 게 아니라 이 개념 자체가 없어진다. `admin.py`/
  `admin_llmops.py`의 모든 라우트에서 `admin: User = Depends(require_admin)`
  파라미터를 `_: None = Depends(require_admin_token)`로 교체한다(두
  라우터 모두 이 파라미터를 게이트 용도로만 쓰고 바디에서 실제로 쓰지
  않는다는 걸 확인했다 — 응답 로직 변경 없음).
- `users.role` 컬럼/CHECK 제약은 그대로 둔다 — 지금 이 컬럼을 참조하는
  코드가 없어지지만, 컬럼을 지우는 건 새 마이그레이션이 필요한 별도
  작업이고 이번 분리의 목적과 무관하다(남아있어도 해롭지 않음, YAGNI).

### admin 앱 쪽 로그인 흐름

- `admin/src/app/login/page.tsx`: 이메일 없이 비밀번호 입력창 하나.
  `POST /admin-auth/login`으로 제출, 성공하면 토큰을 저장(아래 참고)하고
  `/`로 이동.
- `admin/src/lib/api.ts`는 프론트의 `lib/api.ts`와 거의 동일한 구조로
  새로 만들되(복사 후 다듬기), localStorage 키를 사장님 세션과 다른
  이름(`dris_admin_token`)으로 바꾸고, 401을 받으면 `/login`(이 앱 자신의
  로그인 페이지)으로 보낸다 — 기존 `request()`의 401 처리 패턴을 그대로
  재사용.
- `admin/src/app/layout.tsx`(루트가 아니라 각 보호된 페이지를 감싸는
  공통 레이아웃)는 토큰이 없으면 즉시 `/login`으로 보내고, 있으면 실제
  데이터 엔드포인트 호출로 유효성을 검증한다(별도 "me" 엔드포인트를 새로
  만들지 않고, 기존 `request()`의 401 자동 리다이렉트에 맡긴다 — 토큰이
  만료/위조됐으면 첫 API 호출에서 바로 걸린다).

## 화면 구성 (admin 앱으로 이동)

기존 `frontend/src/app/(admin)/ops-4k9x2m/` 아래 4개 화면 + 레이아웃 +
사이드바를 `admin/`으로 옮긴다. 더 이상 난독화된 URL 접두사가 필요 없으므로
(앱 전체가 비밀번호로 막혀있음) 경로를 단순화한다:

| 기존 (frontend) | 신규 (admin) |
|---|---|
| `/ops-4k9x2m` (payments로 리다이렉트) | `/` (payments로 리다이렉트, 기존 `AdminHome`과 동일 패턴) |
| `/ops-4k9x2m/payments` | `/payments` |
| `/ops-4k9x2m/stores` | `/stores` |
| `/ops-4k9x2m/users` | `/users` |
| `/ops-4k9x2m/llmops` | `/llmops` |

- `AdminSidebar.tsx` → `admin/src/components/AdminNav.tsx`로 옮기면서
  `NAV` 배열의 href를 위 표대로 바꾸고, "로그아웃" 버튼은 admin 토큰을
  지우고 `/login`으로, "사장님 화면으로 돌아가기" 링크는 삭제한다(이 앱은
  애초에 사장님 화면이 아니므로 의미가 없어짐).
- 각 페이지(`payments`/`stores`/`users`/`llmops`)의 내부 로직(데이터
  fetch, 렌더링)은 그대로 옮긴다 — import 경로(`@/lib/api`)만 이 새
  프로젝트 기준으로 맞추면 된다. 백엔드 엔드포인트 경로(`/admin/...`)는
  전혀 바뀌지 않는다.
- 디자인 토큰은 기존 `frontend/src/app/globals.css`를 그대로 복사해서
  `admin/src/app/globals.css`로 둔다(같은 다크 테마로 보이게). 루트
  `layout.tsx`의 Geist 폰트 로딩도 동일하게 복사한다.

## 데이터 흐름 / 에러 처리

- admin 앱 → 기존 backend 도메인으로 직접 fetch(브라우저에서, CORS 경유).
  백엔드 CORS 설정(`backend/app/main.py`)에 새 환경변수
  `ADMIN_FRONTEND_ORIGIN`을 추가해 `allow_origins` 리스트에 넣는다(기존
  `FRONTEND_ORIGIN`과 별개로 둘 다 허용).
- 비밀번호 틀림 → `POST /admin-auth/login` 401 → 로그인 폼에 에러 문구.
- 토큰 만료/위조 → 아무 `/admin/...` 호출에서 401 → `lib/api.ts`가
  자동으로 토큰 삭제 + `/login` 리다이렉트(사장님 쪽과 동일 패턴).
- `ADMIN_PASSWORD`/`ADMIN_JWT_SECRET` 미설정(로컬 개발) → 기존
  `JWT_SECRET`과 같은 원칙으로 개발용 고정 기본값 사용, 운영은 반드시
  채워야 함(배포 체크리스트에 명시).

## 배포 순서 (운영 반영)

1. `admin/` 로컬에서 완성 + 빌드 확인.
2. Railway에 새 서비스 `admin` 생성(최초 1회는 도메인을 받기 위해 임시
   값으로라도 한 번 배포해야 함 — 기존 frontend/backend도 이 순서로
   부트스트랩됐다).
3. 받은 admin 도메인을 backend 서비스의 `ADMIN_FRONTEND_ORIGIN`에 설정.
4. backend에 `ADMIN_PASSWORD`/`ADMIN_JWT_SECRET` 설정.
5. admin 서비스에 `NEXT_PUBLIC_API_URL`(기존 backend 도메인) 설정.
6. backend 재배포(CORS/env 반영) → admin 재배포(API URL 반영).
7. admin 앱에서 실제 로그인 + 4개 화면 데이터 확인.
8. 확인되면 `frontend`에서 `(admin)/ops-4k9x2m` 전체 삭제, `require_admin`
   관련 코드 정리, `frontend` 재배포.

8번을 마지막에 두는 이유: 새 체계가 실제로 동작하는 걸 확인하기 전에
기존 경로를 먼저 지우면, 문제가 생겼을 때 관리자 화면 자체에 접근할
방법이 없어진다.

## 테스트

- 백엔드: `backend/tests/test_admin_auth.py` 신규 — 올바른/틀린 비밀번호,
  발급된 토큰으로 보호된 엔드포인트 접근 성공, 토큰 없이/잘못된 토큰으로
  401. 기존 `backend/tests/test_admin.py`/`test_admin_llmops.py`는
  `_promote_to_admin(db_session, user)` 헬퍼와 `auth_headers`(사장님
  로그인 토큰) 대신, admin 로그인으로 받은 토큰을 헤더에 쓰도록 고친다 —
  403 기대 테스트는 "토큰 없음"/"사장님 토큰으로 접근"으로 바뀐다.
- 프론트: `admin/`에 대해 `npx tsc --noEmit`. 로그인 플로우는 로컬에서
  브라우저로 수동 확인(비밀번호 맞음/틀림, 4개 화면 데이터 로딩).

## 범위 밖

- `users.role` 컬럼 삭제(위에서 언급, 별도 작업으로 남김).
- admin 계정 여러 개/권한 세분화(지금은 "나만" 쓰는 단일 비밀번호로
  충분하다고 확정함 — 필요해지면 그때 아이디/비밀번호 쌍 테이블로
  확장).
- admin 전용 커스텀 도메인 연결(Railway가 주는 기본 도메인으로 충분,
  필요해지면 나중에 추가).
