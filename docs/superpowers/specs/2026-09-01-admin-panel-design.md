# 관리자 페이지 설계

## 배경

review-docter는 이제 실제 결제(토스페이먼츠 테스트 키), 실제 배민 계정 연동
(리뷰/매출/정산 스크래핑), 실제 자동화(5점 리뷰 자동 답글 실제 제출, 매일
새벽 4시 자동 동기화 스케줄러)가 걸린 실 SaaS 데모다. 지금까지는 이런 것들의
상태를 확인하거나 문제가 생겼을 때 대응하려면 매번 프로덕션 DB에 SSH 터널로
직접 접속해 SQL을 돌려야 했다(이번 세션에서만도 데모 계정 플랜 전환, 자동
답글 설정 확인 등에 여러 번 이 방식을 썼다).

이 설계는 "모든 테이블을 CRUD로 노출하는 범용 어드민"이 아니라, 실제로
사람이 개입해야 하는 지점 — 결제 감사, 배민 자동화 안전장치, 동기화 실패
확인, 계정/플랜 조회 — 만 다루는 최소 범위의 관리자 페이지를 만든다.

## 범위

**포함**:
1. 결제 이력 조회 (환불은 토스 콘솔에서 직접 — 여기선 조회만)
2. 배민 연결 매장별 운영 현황 (마지막 동기화 상태 + 자동답글 on/off 스위치)
3. 유저 검색 + 플랜 수동 변경 (고객 문의 대응용)
4. 관리자 전용 경로 보호 + 로그인 브루트포스 방어

**의도적으로 뺀 것**:
- 관리자 액션 감사 로그 — 관리자가 본인 한 명뿐이라 지금은 실익이 적다.
  다른 관리자가 추가되면 이 설계를 다시 열어야 한다.
- 자동화 전체 긴급 정지(킬스위치) — 매장별 토글로 충분하다고 판단.
- 크롤 워커 수동 재실행 버튼 — 이미 사장님용 "가게 연결" 화면에 있는 버튼을
  관리자도 그대로 쓸 수 있어 중복 구현하지 않는다.
- 결제 환불/취소 액션 — 토스 콘솔이 이미 있는 도구라 재구현하지 않는다.
- 관리자 초대/가입 플로우 — 관리자가 본인 한 명뿐이라 배포 후 프로덕션
  DB에 수동 SQL 한 번으로 처리한다.

## 1. 데이터 모델

```sql
ALTER TABLE users ADD COLUMN role VARCHAR(10) NOT NULL DEFAULT 'owner'
  CHECK (role IN ('owner', 'admin'));
```

기존 모든 사장님 계정은 `'owner'`로 그대로 유지된다(기본값). 소유자 본인
계정만 배포 후 프로덕션 DB에 `UPDATE users SET role = 'admin' WHERE email =
'...'`로 수동 전환한다.

`schema.sql`/`backend/app/models.py`(`User` 모델)에 반영하고, `seed.sql`은
건드리지 않는다(로컬 시드 계정은 전부 `'owner'`로 충분).

## 2. 인증 · 인가 · 경로 보호

### 2.1 관리자 판별

`backend/app/auth.py`(또는 신규 `backend/app/admin_auth.py`)에 기존
`require_pro_plan`(`backend/app/plan.py:71`)과 동일한 패턴으로 의존성을
추가한다:

```python
def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user
```

`GET /auth/me` 응답 모델에 `role: str` 필드를 추가한다 — 프론트가 로그인 후
이 값으로 관리자 여부를 판단한다.

### 2.2 로그인은 공용

관리자도 기존 `POST /auth/login`을 그대로 쓴다(별도 관리자 로그인 폼 없음).
로그인 성공 후 프론트가 `role`을 보고 관리자면 관리자 페이지로, 아니면 기존
`/dashboard`로 보낸다.

### 2.3 경로

관리자 페이지는 예측 불가능한 슬러그 `/ops-4k9x2m` 아래 배치한다
(`frontend/src/app/(admin)/ops-4k9x2m/`). URL을 숨기는 것 자체는 진짜
보안 경계가 아니다 — 실제 방어는 아래 `require_admin`과 프론트 가드다.
이 슬러그는 "자동화된 스캐너/봇이 `/admin` 같은 흔한 경로를 찾는 것"을
막는 값싼 추가 방어층일 뿐이다. 나중에 바꾸고 싶으면 라우트 폴더명만
바꾸면 된다.

`frontend/src/app/(admin)/ops-4k9x2m/layout.tsx`에서 `/auth/me`로 `role`을
확인해 `'admin'`이 아니면 `/dashboard`로 즉시 리다이렉트한다. 백엔드
`/admin/*` 라우트는 전부 `Depends(require_admin)`으로 한 번 더 막는다 —
프론트 가드를 우회해 백엔드를 직접 두드려도 막히도록(광고 순위 모니터링에서
`require_pro_plan`을 쓴 것과 같은 이유).

### 2.4 로그인 브루트포스 방어

`POST /auth/login`(`backend/app/routers/auth.py`)에 이메일 기준 실패 횟수
제한을 추가한다:

- 같은 이메일로 5회 연속 로그인 실패 시 15분간 잠금(`429 Too Many Requests`,
  "너무 많이 실패했어요. 15분 후 다시 시도해주세요" 류 메시지)
- 로그인 성공 시 해당 이메일의 실패 카운터를 리셋
- 저장은 새 DB 테이블 없이 프로세스 메모리 내 dict(`{email: (count,
  locked_until)}`)로 시작한다 — 이 프로젝트는 Railway 단일 인스턴스로만
  뜨므로(numReplicas 미지정) 여러 서버 간 카운터 불일치 문제가 없다. 배포/
  재시작 시 카운터가 초기화되는 건 알려진 한계로 남겨둔다(이 시점을 노리는
  공격은 이론적으로 가능하지만, 지금 규모에서 DB 테이블로 영속화할 만큼의
  실익은 없다고 판단).

이 방어는 관리자 계정에 한정되지 않고 `/auth/login`을 쓰는 모든 계정에
적용된다 — 관리자 계정도 결국 같은 로그인을 쓰므로, 이 방어가 없으면 경로를
숨겨도 로그인 자체를 무차별 대입해 뚫릴 수 있다.

## 3. 화면 구성

세 화면으로 구성한다. "자동화 안전 스위치"와 "크롤 워커 헬스체크"는 원래
두 화면으로 논의됐으나, 둘 다 "배민 연결된 매장별 한 줄"이라는 같은 데이터
소스를 쓰므로 한 화면(매장 운영 현황)으로 합쳤다.

### 3.1 `/ops-4k9x2m/payments` — 결제 이력 (조회 전용)

`payments`를 `users`(이메일·닉네임) 조인해 최근순으로 나열한다.

| 컬럼 | 내용 |
|---|---|
| 사용자 | 이메일 · 닉네임 (카카오 전용 계정은 이메일이 없으므로 "카카오 계정"으로 표시) |
| 플랜 | payments.plan |
| 금액 | payments.amount (원화 포맷) |
| 상태 | pending(warning) / approved(success) / failed(danger) pill |
| 요청 시각 | requested_at |
| 승인 시각 | approved_at (없으면 "—") |
| 실패 사유 | fail_reason (없으면 빈 칸) |

상태 필터(전체/pending/approved/failed)만 있고 쓰기 액션은 없다.

`GET /admin/payments?status=&limit=50`
→ `[{ order_id, user_email, user_nickname, plan, amount, status,
requested_at, approved_at, fail_reason }]`

### 3.2 `/ops-4k9x2m/stores` — 매장 운영 현황

`store_platform_connections`(플랫폼=배민, **`credential_ciphertext IS NOT
NULL`인 것만** — 실 계정이 연결된 매장만 운영 대상이다. Mock뿐인 연결이나
아직 연결 안 한 매장은 애초에 동기화 자체가 안 되므로 이 화면에 노이즈로
띄우지 않는다)마다, `review_sync_jobs`에서 `store_id` 기준 가장 최근 1건과
`reply_settings.auto_reply_enabled`를 조인한다.

| 컬럼 | 내용 |
|---|---|
| 매장명 | stores.name |
| 소유자 | users.nickname / email |
| 마지막 동기화 | triggered_by(수동/자동) · 상태(success=success, failed=danger, running=accent, pending=muted) · finished_at 상대 시각 |
| 실패 사유 | error_message 요약(있을 때만, truncate) |
| 자동답글 | reply_settings.auto_reply_enabled 토글 스위치 (유일한 쓰기 액션) |

동기화 이력이 아예 없는 매장(한 번도 동기화 안 함)은 "동기화 기록 없음"으로
표시한다.

```
GET   /admin/stores
PATCH /admin/stores/{store_id}/auto-reply   { "enabled": bool }
```

### 3.3 `/ops-4k9x2m/users` — 유저 조회 + 플랜 변경

이메일/닉네임 부분 일치 검색(검색어 없으면 최근 가입순 상위 N명).

| 컬럼 | 내용 |
|---|---|
| 이메일 | users.email (카카오 전용 계정은 "카카오 계정") |
| 닉네임 | users.nickname |
| 가입일 | users.created_at |
| 현재 플랜 | effective_plan() 재사용 — Basic/Pro + 만료일 |
| 연결된 매장 수 | stores 카운트 |

각 행에 "플랜 변경" 액션(Basic ↔ Pro 선택 + Pro 선택 시 "며칠간" 입력,
기본 30일).

**설계 노트 — `expires_at` 계산**: `plan.py`의 `effective_plan()`은
`plan == 'pro' and expires_at >= 오늘`일 때만 Pro로 판정한다. 관리자가
그냥 `plan='pro'`만 세팅하고 `expires_at`을 비워두면 즉시 Basic으로
떨어지는 것처럼 보이는 버그가 된다. 그래서 Pro 전환은 반드시 기간(일수)을
받아 `expires_at = kst_today() + N일`로 계산한다 — 기존 결제 승인 로직
(`_approve_payment`, `backend/app/routers/billing.py`)과 동일한 계산
방식이라 `plan.py`의 판정 로직을 전혀 건드리지 않는다. Basic 전환은
`plan='basic', expires_at=NULL`.

이 경로로 만들어진 Pro 부여는 `payments` 테이블에 어떤 행도 남기지 않는다
— 결제 이력(3.1)에는 나타나지 않는, 관리자의 수동 조정이라는 뜻이다. 이건
의도된 동작이다(가짜 결제 기록을 만들지 않음).

```
GET   /admin/users?q=
PATCH /admin/users/{user_id}/plan   { "plan": "basic" | "pro", "days"?: int }
```

`days`는 1~365 범위로 서버에서 검증한다(`plan="pro"`일 때만 의미 있음,
기본값 30).

## 4. UI/UX

새 디자인 시스템을 만들지 않고 기존 웹의 다크 테마·컴포넌트를 그대로
재사용한다:

- **레이아웃**: `(app)/layout.tsx`와 같은 2단 구조(사이드바 + 콘텐츠)를
  `(admin)/ops-4k9x2m/layout.tsx`에 새로 만든다. `Sidebar` 대신 새
  `AdminSidebar`(메뉴 3개 + "사장님 화면으로 돌아가기" 링크 + 로그아웃) —
  기존 `Sidebar`는 `useStoreContext`(현재 선택된 매장)에 의존하는데
  관리자 페이지는 특정 매장에 묶이지 않으므로 그대로 재사용할 수 없다.
- **색 토큰**: `globals.css`의 기존 CSS 변수(`--background`, `--surface`,
  `--accent`, `--success`, `--warning`, `--danger` 등)를 그대로 쓴다. 새
  토큰을 추가하지 않는다.
- **상태 표시**: 리뷰/광고 화면에서 이미 쓰는 pill 패턴(`bg-danger/10
  text-danger` 류)을 결제 상태·동기화 상태에 동일하게 적용한다.
- **Logo 컴포넌트**: 로그인 화면·사이드바와 같은 과녁+T 마크를 관리자
  사이드바 상단에도 그대로 쓴다.
- **RingGauge/ThresholdBar는 쓰지 않는다** — 관리자 화면은 표 형태 데이터가
  중심이라 0~100 게이지로 표현할 지표가 없다. "토스 스타일"의 핵심(다크
  테마, 절제된 여백, 상태를 색+pill로 즉시 읽히게 하는 것)만 유지한다.
- **새로 필요한 컴포넌트**: 자동답글 on/off 토글 스위치 하나뿐 — 기존
  웹 코드베이스에 토글 컴포넌트가 없어 새로 만든다(작은 컴포넌트, 구현
  계획 단계에서 처리).

## 5. 에러 처리

| 상황 | 처리 |
|---|---|
| 비관리자가 `/admin/*` API 직접 호출 | 403, 프론트는 `/dashboard`로 리다이렉트(레이아웃 가드가 먼저 막으므로 실제로는 거의 발생 안 함) |
| 로그인 5회 연속 실패 | 429 + "너무 많이 실패했어요. 15분 후 다시 시도해주세요" |
| 자동답글 토글 PATCH 실패 | 토스트로 에러 노출, 낙관적 업데이트 없음 — 서버 응답 받은 뒤에만 화면 갱신 |
| 플랜 변경 `days` 범위 밖 | 422 (Pydantic `Field(gt=0, le=365)`) |
| 결제/매장/검색 결과 없음 | 기존 화면들과 동일한 "데이터가 없습니다" 문구 |

## 6. 테스트 계획

백엔드(pytest, 기존 컨벤션 — SQLite 인메모리):

- `require_admin`이 `role != 'admin'`이면 403, `role == 'admin'`이면 통과
- 로그인 5회 연속 실패 시 429, 성공 시 카운터 리셋, 6번째 시도는 잠금 유지
- `GET /admin/payments` — 상태 필터, 사용자 정보 조인 정확성
- `GET /admin/stores` — 최신 동기화 잡 1건만 선택되는지(여러 건 있을 때),
  동기화 기록 없는 매장 처리
- `PATCH /admin/stores/{id}/auto-reply` — `reply_settings.auto_reply_enabled`
  실제 갱신
- `GET /admin/users?q=` — 이메일/닉네임 부분 일치 검색
- `PATCH /admin/users/{id}/plan` — pro 전환 시 `expires_at` 계산, basic
  전환 시 `NULL` 클리어, `days` 범위 검증
- 관리자 엔드포인트 5개 전부에 대한 비관리자 403 (파라미터화 테스트)

프론트: `npx tsc --noEmit` 클린 + 관리자 계정으로 실제 로그인해 브라우저로
확인(이 프로젝트에 프론트 단위 테스트가 없는 기존 관행을 그대로 따름).
