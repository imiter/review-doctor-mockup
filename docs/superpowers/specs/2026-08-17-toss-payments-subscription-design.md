# 결제/구독(토스페이먼츠 테스트 연동) 설계

## 배경

CLAUDE.md의 "방향 전환(실 SaaS 확장)" 로드맵 2번 항목("결제/구독 — PG사 테스트 연동")을
이제 진행한다. 지금은 `subscriptions` 테이블(plan/daily_reply_limit/expires_at)만
존재하고 실제로 플랜을 막는 로직이나 결제 화면은 전혀 없다 — 가입 시
`_create_default_store_and_subscription`이 `plan='basic'` 행 하나를 만드는 게 전부다
(`backend/app/routers/auth.py:84-99`).

"절대 금지" 목록의 "실제 결제, 구독... 자동화 금지"에 대해, 이 세션에서 예외를
허용하기로 결정했다 — 단 **토스페이먼츠 테스트 키(`test_ck_.../test_sk_...`)로만
연동한다.** 테스트 키는 실제 카드사망을 타지 않아 구조적으로 실결제가 불가능하다
(운영 키 `live_ck_.../live_sk_...`로 바꾸는 건 완전히 별도 승인이 필요한 범위 밖 결정).
정기결제(빌링키/자동 재결제)는 하지 않는다 — 사용자가 매달 수동으로 다시 결제하는
일회성 결제 방식이다.

## 1. 결제 플로우

1. 사용자가 "Pro 시작하기" 클릭 → `POST /billing/checkout`. 백엔드가 **서버에서
   금액을 결정**(하드코딩 상수 `PRO_MONTHLY_PRICE = 19900`, 클라이언트가 보낸 금액을
   신뢰하지 않음)하고 `payments`에 `status='pending'` 행을 만들어 `order_id`(UUID)를
   발급, `{order_id, amount, order_name, client_key}`를 반환.
2. 프론트가 이 값으로 토스 결제위젯을 페이지 내 인라인으로 렌더링(별도 페이지 이동
   없음), 사용자가 테스트 카드로 결제.
3. 토스가 `successUrl=/account/billing/success?paymentKey=...&orderId=...&amount=...`로
   리다이렉트. 실패/취소 시 `failUrl=/account/billing/fail?...`.
4. 프론트가 success 쿼리값을 그대로 `POST /billing/confirm`에 전달 → 백엔드가:
   - `order_id`로 `payments` 조회, **현재 로그인 유저 소유가 맞는지, status가
     `pending`인지, 쿼리로 받은 `amount`가 DB에 저장된 금액과 정확히 일치하는지**
     검증(하나라도 어긋나면 토스 API를 아예 호출하지 않고 400).
   - 검증 통과 시 토스 `POST https://api.tosspayments.com/v1/payments/confirm`을
     시크릿키로 Basic Auth해서 호출(`{paymentKey, orderId, amount}`).
   - 토스 승인 성공(200) → `payments.status='approved'`, `toss_payment_key`,
     `approved_at` 기록. `subscriptions.plan='pro'`,
     `expires_at = add_one_month(max(오늘, 기존 expires_at ?? 오늘))`로 갱신(기존
     Pro 기간이 남아있으면 그 뒤에 이어붙임 — 결제 손해 방지).
   - 토스 승인 실패(4xx) → `payments.status='failed'`, `fail_reason`에 토스 에러
     메시지 저장, 구독은 건드리지 않고 프론트에 에러 반환.
5. `/account/billing/success` 화면이 confirm 결과를 보여주고 `/account/billing`으로
   복귀 링크 제공. `/account/billing/fail`은 에러 메시지 + 재시도 버튼.

### 만료 처리 (배치 없이 조회 시점 판정)

크론 인프라가 없는 이 프로젝트 컨벤션대로, 만료는 별도 작업 없이 조회 시점에
판정한다: `effective_plan(sub) = 'pro' if sub.plan=='pro' and sub.expires_at and
sub.expires_at >= 오늘 else 'basic'`. `add_one_month`는 이전 세션에서 3개월
백필 때 썼던 것과 같은 달력월 안전 계산(월말 클램핑)을 재사용한다 — 단순
`+ timedelta(days=30)`이 아니다.

## 2. 데이터 모델 (신규 테이블 1개, 22번째)

```sql
CREATE TABLE payments (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id         VARCHAR(64) NOT NULL UNIQUE,
    plan             VARCHAR(10) NOT NULL DEFAULT 'pro',
    amount           INT         NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','failed')),
    toss_payment_key VARCHAR(200),
    fail_reason      VARCHAR(200),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at      TIMESTAMPTZ
);
```

`subscriptions`는 스키마 변경 없음. `payments.requested_at`/`approved_at`은
schema.sql에서 TIMESTAMPTZ로 선언하고, 코드에서 값을 만들 때는 반드시
`datetime.now(timezone.utc)`처럼 tz-aware로 생성한다(naive datetime을
TIMESTAMPTZ 컬럼에 그대로 넣어 9시간 밀리던 버그가 이 세션에서 두 번 있었다 —
`ingest_rank_snapshots.py`/주문내역 — 세 번째로 반복하지 않는다). `models.py`의
`Payment`는 기존 컨벤션대로 bare `Mapped[datetime]`으로 선언(이 저장소에
`DateTime(timezone=True)` 명시 선례가 없고, 실질적 안전장치는 ORM 컬럼 선언이
아니라 "값 생성 시점에 항상 aware로 만든다"는 규율이기 때문).

## 3. 백엔드 API (`backend/app/routers/billing.py`, prefix 없이 전체 경로 명시 — 이 저장소의
다수 라우터 컨벤션)

- `GET /billing/me` — `{plan, is_pro, expires_at, daily_reply_limit, replies_used_today}`.
  `get_current_user` 의존성 재사용(기존 모든 라우터와 동일 패턴). `replies_used_today`는
  `ReviewReply → Review → Store(user_id=...)` 체인을 조인해 **KST 기준 오늘**(UTC
  자정이 아니라 한국 자정 경계) 생성분을 센다.
- `POST /billing/checkout` — 위 1번 흐름의 체크아웃 생성.
- `POST /billing/confirm` — 위 1번 흐름의 승인 처리.
- `GET /billing/history` — 로그인 유저의 `payments` 목록(최신순, 날짜/금액/상태).

### 답글 생성 한도 강제 (`backend/app/routers/reviews.py:107` 근처, 기존 `generate_reply`)

`ReviewReply` 생성 직전에 `effective_plan`이 `basic`이면 오늘(KST) 이 유저가 생성한
답글 수를 세어 `daily_reply_limit`(기본 10) 이상이면 403
`{"detail": "...", "error_code": "reply_limit_exceeded"}`. Pro는 무제한(카운트만
표시용으로 계속 집계).

### 광고 순위 모니터링 잠금

백엔드 `/ads/*`는 변경 없음(단순함 유지) — 잠금은 프론트에서만 처리(아래 4번).

## 4. 프론트엔드

**사이드바** (`Sidebar.tsx`): "내 정보 관리" 섹션에 `가게 연결 → 계정 관리 → 구독 관리`
순으로 `/account/billing` 추가.

**`StoreContext`**(`frontend/src/lib/store-context.tsx`): 초기 `Promise.all`에
`GET /billing/me` 추가해 `billing` 필드로 노출, 결제 완료 후 갱신용
`refreshBilling()` 액션 추가(기존 `refreshUser()`와 동일 패턴).

**`/account/billing`**: 두 섹션.
1. 현재 구독 카드 — 플랜 뱃지(Basic 회색 / Pro 보라 `bg-accent-soft text-accent`),
   Pro면 다음 결제 예정일, 결제 내역 테이블(없으면 빈 상태 문구).
2. 요금제 비교 카드 2개(Basic/Pro) — 우리 실제 기능만(답글 생성 한도, 광고 순위
   모니터링, 나머지는 "동일 포함"). Pro 카드 "Pro 시작하기" 클릭 시 그 자리에
   토스 결제위젯 인라인 전개.

**`/account/billing/success`, `/account/billing/fail`**: 위 1번 흐름의 결과 화면.

**리뷰 관리 화면**: 상단에 "오늘 답글 생성 N/10 (Basic)" 또는 "무제한 (Pro)" 배지.
403(`reply_limit_exceeded`) 수신 시 토스트 + 구독 관리 링크.

**`/ads`**: `billing.is_pro`가 false면 API 호출 자체를 생략하고, 잠금 카드(자물쇠
아이콘 + "Pro 전용 기능입니다" + `/account/billing` 이동 버튼)만 렌더링.

## 5. 토스페이먼츠 연동 세부 (구현 시점 재확인 필요)

- 결제위젯 JS SDK를 프론트에 추가(정확한 npm 패키지명·`TossPayments()` 초기화
  함수·`widgets()`/`requestPayment()` 파라미터는 토스 개발자센터 최신 가이드가
  SPA라 이 스펙 작성 시점에 자동 조회로 원문을 못 가져왔다 — **구현 태스크에서
  실제 가이드 페이지를 브라우저로 열어 재확인 후 진행**, 이 저장소의 "실 계정
  라이브 검증" 컨벤션과 동일하게 취급).
- 확인된 사실: 승인 엔드포인트는 `POST https://api.tosspayments.com/v1/payments/confirm`,
  바디는 `paymentKey`/`orderId`/`amount`(모두 필수), 시크릿키로 Basic Auth. 초기화는
  `TossPayments(clientKey)` 형태고 `.widgets()`로 결제위젯을 초기화한다.
- 환경변수: `backend/.env`에 `TOSS_SECRET_KEY`(서버 전용, 커밋 금지),
  프론트에 `NEXT_PUBLIC_TOSS_CLIENT_KEY`(브라우저 노출 전제, Next.js 컨벤션상
  `NEXT_PUBLIC_` 접두어 필수). 사용자가 아직 키를 발급받지 않아서, 값이 없어도
  화면/코드는 전부 완성해두고 `.env.example`에 플레이스홀더로 남긴다 — 발급 후
  값만 채우면 바로 동작해야 한다(이번 요청의 핵심 요구사항).

## 6. 에러 처리

- 체크아웃 시 서버 금액 산정이라 클라이언트가 금액을 조작할 여지 없음.
- confirm 시 `order_id` 소유권 불일치, 이미 `approved`/`failed` 상태 재확인 시도,
  금액 불일치는 전부 토스 API 호출 전에 400/403으로 차단.
- 토스 API 자체가 5xx/네트워크 에러를 내는 경우 `payments.status`는 `pending`으로
  남기고(재시도 가능하게) 프론트에 "잠시 후 다시 시도해주세요" 표시.
- 위젯 자체 실패(사용자 취소 등)는 토스가 `failUrl`로 보내주므로 그 경로에서 처리.

## 7. 테스트 계획

**pytest (백엔드)**:
- `add_one_month`/`effective_plan` 유닛 테스트(만료된 Pro→basic, 월말 클램핑,
  기존 Pro 기간에 이어붙이는 갱신 케이스).
- `POST /billing/checkout`: 서버가 금액을 결정함을 검증(요청 바디에 다른 금액을
  보내도 무시되고 19900으로 저장됨).
- `POST /billing/confirm`: 토스 API 호출을 mock — 승인 성공 시 구독 갱신, 금액
  불일치 시 토스 API를 호출하지 않고 400(mock 호출 안 됐음을 assert), 다른 유저의
  `order_id`로 시도 시 403, 토스 API가 실패 응답을 줄 때 `payments.status='failed'`로만
  남고 구독은 안 바뀜.
- `GET /billing/me`: basic/pro/만료된 pro 각각에서 `is_pro`/`replies_used_today` 정확성.
- 답글 생성 한도: Basic 10건까지 성공, 11번째 403(`reply_limit_exceeded`), Pro는
  10건 넘어도 성공, KST 자정 경계 테스트(UTC 자정 근처 시각으로 mock해서 날짜
  경계 버그 재발 방지).

**라이브 검증 (Playwright 상호작용과 동일 컨벤션, 자동테스트 대상 아님)**:
토스 결제위젯 렌더링·실제 테스트 카드 결제·success/fail 리다이렉트는 사용자가
토스 테스트 키를 `.env`에 넣은 뒤 브라우저로 직접 1회 검증한다(자동화 불가 —
위젯이 iframe으로 카드 정보를 받음).

## 8. CLAUDE.md 갱신 (구현 마지막 태스크)

- "결제/구독 연동 (예외 허용, 테스트 모드)" 절 신규 추가 — 위 배경/제약 요약,
  절대 금지 목록 옆에 참조 추가(카카오 로그인/배민 리뷰 절과 동일 패턴).
  **실제 돈이 움직이지 않는다는 점(테스트 키 전용, 정기결제 아님)을 명시.**
  로드맵 2번 항목에 완료 표시.
  - DB 설계 "21개 테이블" → "22개 테이블", `payments` 테이블 용도 추가.
  - "포함 기능" 목록에 "구독 관리" 추가.

## 스코프 밖 (YAGNI)

- 정기결제(빌링키), 플랜 다운그레이드/해지 UI, 연간 플랜, 환불, 웹훅(토스가
  비동기로 보내는 결제 상태 변경 웹훅) — 전부 다음 단계로 미룬다.
