# Delivery Review & Store Insight MVP

## 프로젝트 정체성
이 프로젝트는 배달매장 사장을 위한 DB 설계 중심 MVP다.
완성형 서비스가 아니라 데이터 모델링, 현장 문제 이해, 범위 통제 능력을
보여주는 교육 과정 과제물이다. 외부 API 연동 없이 Mock 데이터와 Mock API로만
동작한다. 리뷰닥터/세일즈랩을 벤치마크했지만 화면을 복제한 것이 아니라
분석 후 재설계했다.

기술 스택: PostgreSQL, FastAPI, Next.js.

### 방향 전환 (2026-08-06, 실 SaaS 확장 결정)
원래 이 프로젝트는 Mock 데이터로만 동작하는 교육 과정 과제물이었으나, 외주
포트폴리오용으로 실제 동작하는 데모가 필요해져서 실제 SaaS로 단계적으로
확장하기로 결정했다. 아래 순서로 실제 기능을 하나씩 붙인다:
1. 카카오 소셜 로그인 (진행 중 — 아래 "카카오 소셜 로그인" 절 참고)
2. 결제/구독 (PG사 테스트 연동)
   (2026-08-17 완료 — 아래 "결제/구독 연동" 절 참고)
3. 실제 배달 플랫폼(배민/쿠팡이츠/요기요) 사장님광장 데이터 연동
4. LLM 기반 답글 자동생성 고도화 (RAG 포함)

교육 과제물 시절의 "절대 금지" 목록은 여전히 이 문서 아래에 원문 그대로
남겨둔다 — 아직 손대지 않은 항목(결제, 실플랫폼 연동, 실AI 호출)에 대해서는
지금도 유효한 제약이고, 각 항목은 실제로 구현하는 시점에 크롤링/모바일 앱
때와 같은 방식으로 "예외 허용" 절을 추가하며 갱신한다. 즉 이 문서를 실 SaaS
전환의 정본으로 계속 쓰고, 별도 문서로 분리하지 않는다.


## 절대 금지 (의도적으로 제외한 범위, 교육 과제물 시절 원칙 — 위 "방향 전환" 참고)
- 실제 쿠팡이츠/요기요 API 연동 금지 (배민은 예외 허용 — 아래 "배민 리뷰 연동" 절 참고)
- 실제 리뷰 크롤링 금지 (배민 리뷰는 예외 허용 — 아래 "배민 리뷰 연동" 절 참고)
- 실제 AI API 호출 금지 (답글 생성은 템플릿 기반 Mock)
- 실제 자동 답글 등록 금지 (5점 리뷰에 한해 예외 허용 — 아래 "배민 자동 답글 실제 제출" 절 참고)
- 실제 CPC 자동 입찰 금지
- 실제 결제, 구독, 쿠팡이츠 출금 자동화 금지
- 실제 문자/카카오톡 발송 금지
- 복잡한 권한/다중 사업자 권한 관리 금지
위 기능은 전부 Mock으로 흉내만 낸다.
단, 광고비율(ACoS) 계산은 실제 공식으로 계산하고, 광고 순위 모니터링의
반경별(distance_km) 순위는 crawler/(Appium 실기기 자동화)로 실측 수집한다
(추후 결정으로 예외 허용 — 아래 "창의 기능" 절 참고). crawler/는 사이트의
요청 경로(FastAPI 프로세스) 밖에서 독립적으로 실행되는 별도 배치 도구이고,
사이트는 그 결과를 DB에서 조회만 한다 — 사이트가 실시간으로 배민 앱을
크롤링하지 않는다.

### 결제/구독 연동 (예외 허용, 테스트 모드)
원래 "실제 결제, 구독... 자동화 금지"였으나, 실 SaaS 전환 로드맵 2번으로
토스페이먼츠 **테스트 키**(`test_ck_.../test_sk_...`) 연동을 실제로 붙이기로
결정했다(2026-08-17). 테스트 키는 실제 카드사망을 타지 않아 구조적으로
진짜 돈이 움직이지 않는다 — 운영 키(`live_ck_.../live_sk_...`) 전환은 완전히
별도 승인이 필요한 범위 밖이고 아직 하지 않았다. 정기결제(빌링키/자동
재결제)도 하지 않는다 — 사용자가 매달 수동으로 다시 결제하는 일회성
결제만 지원한다.

Basic/Pro 플랜 차이를 이번에 처음 실제로 정의했다: 답글 생성 일일 한도
(Basic 10건, Pro 무제한, `backend/app/routers/reviews.py`의
`generate_reply`가 강제)와 광고 순위 모니터링(Basic은 프론트에서 잠금,
`frontend/src/app/(app)/ads/page.tsx`). 결제 승인은 `backend/app/routers/
billing.py`가 처리한다 — 프론트가 `POST /billing/checkout`으로 서버가
결정한 금액(`PRO_MONTHLY_PRICE=19900원`)의 주문을 만들고, 토스 결제위젯
결제 후 `POST /billing/confirm`에서 **금액을 서버 DB에 저장된 값과
대조 검증한 뒤**(클라이언트가 보낸 금액을 신뢰하지 않음) 토스 승인 API를
호출한다. 만료 판정은 크론 없이 조회 시점 lazy 판정(`backend/app/
plan.py`의 `effective_plan`)이고, 모든 날짜 경계는 KST(Asia/Seoul)
자정 기준이다. 설계 상세는
`docs/superpowers/specs/2026-08-17-toss-payments-subscription-design.md`
참고.

가상계좌 결제수단은 즉시 승인되지 않고 입금을 기다려야 한다 — 토스 결제승인
API가 `WAITING_FOR_DEPOSIT` 상태를 돌려주면 `POST /billing/confirm`은 실패
처리하지 않고 가상계좌 정보와 `secret`을 저장한 채 대기 상태로 남긴다. 실제
입금이 완료되면 토스가 `POST /billing/webhook`(`DEPOSIT_CALLBACK` 이벤트)을
호출하고, 저장해둔 `secret`과 대조해서 맞으면 그때 구독을 Pro로 승인한다.
승인 로직(`_approve_payment`, `backend/app/routers/billing.py`)은 즉시결제
경로와 가상계좌 경로가 공유한다. 설계 상세는
`docs/superpowers/specs/2026-08-18-toss-virtual-account-webhook-design.md`
참고.

### 카카오 소셜 로그인 (예외 허용)
원래 "이메일 기반 간단 로그인 구현 (소셜 로그인 제외, 추후 추가)"였으나, 실
SaaS 전환의 첫 단계로 카카오 로그인을 실제로 붙이기로 결정했다(위 "방향 전환"
절 참고). 카카오 비즈니스 앱 전환(사업자 등록 연동, 심사)은 아직 하지 않아서
이메일 동의 항목은 받지 못한다 — 카카오 고유 회원번호와 닉네임만으로 로그인/
가입이 되게 만든다. 이메일 회원가입은 그대로 유지하고 병행한다. 설계 상세는
`docs/superpowers/specs/2026-08-06-kakao-social-login-design.md` 참고.

### 이메일 인증 (실제 발송, 예외 허용)
원래 이메일 회원가입은 이메일/비밀번호만 받고 즉시 계정을 만들었으나, 실 SaaS
포트폴리오 데모에 맞게 다단계 인증 위자드로 바꿨다: 닉네임/이메일 입력 →
이메일 인증(Resend로 실제 발송) → 비밀번호+확인 → 가입 완료. 이메일은 애초에
"절대 금지" 목록에 없던 항목이라 이번에 실제로 붙였다. Resend 커스텀 도메인은
아직 인증하지 않아서 기본 발신 주소(`onboarding@resend.dev`)로는 Resend 계정
소유자 본인 이메일 외에는 실제 수신이 제한될 수 있다는 점은 알려진 제약으로
남겨둔다.

휴대폰 인증은 초기 설계에는 Mock 단계로 포함됐었으나, 실효성이 없다고 판단해
가입 위자드에서 완전히 뺐다(2026-08-07, 세션 중 재결정) — 휴대폰은 더 이상
가입 시점에 받지 않고, 가입 후 `PATCH /auth/me`로 선택적으로 추가한다. "절대
금지"의 실제 문자 발송 금지 원칙은 계속 유효하며 실제로 손댄 적이 없다. 설계
상세는 `docs/superpowers/specs/2026-08-07-email-signup-verification-design.md`
참고(휴대폰 Mock 인증 단계를 포함한 원안 — 승인 당시 기록이라 소급 수정하지
않음, 최신 동작 기준은 이 문단).

### 배민 리뷰 연동 (예외 허용)
원래 "실제 배민/쿠팡이츠/요기요 등 플랫폼 API 연동 금지", "실제 리뷰 크롤링
금지"였으나, 실 SaaS 전환 로드맵 3번(실제 배달 플랫폼 데이터 연동)의 첫
단계로 배민 리뷰만 실제로 연동하기로 결정했다(2026-08-09). 실제 계정으로
크롬 개발자도구 Network 탭을 직접 확인해, 사장님광장(self.baemin.com)의
리뷰 API(`self-api.baemin.com/v1/review/shops/{shopNo}/reviews`)와 주문내역
API 사이에 서로를 연결하는 공통 키가 없다는 걸 확인했다 — 그래서 리뷰를
주문/정산과 억지로 연결하지 않고, `reviews` 테이블이 `store_id`/
`platform_id`/`menu_summary`를 직접 갖도록 데이터 모델을 바꿨다(`order_id`는
있으면 연결하는 선택적 FK로 강등, 실제로 채워지는 경우는 없음).

"가게 연결" 화면에서 배민 카드만 실제 ID/PW를 받아 Playwright로 사장님광장에
로그인한다(`backend/scrapers/baemin_auth.py`). 리뷰 수집(`backend/scrapers/
baemin_reviews.py`)은 직접 HTTP 호출이 아니다 — 로그인된 페이지가 "리뷰관리"
화면을 열 때 스스로 발생시키는, 이미 서명된 API 응답을 `page.on("response")`로
가로챈다. 직접 시도한 두 방식은 모두 실 계정으로 재현된 이유로 폐기했다:
`APIRequestContext`로 리뷰 API를 브라우저 밖에서 직접 호출하면 배민이 매
요청마다 동적으로 계산하는 서명 헤더(`x-e-request`)가 없어 403이 나고,
페이지 안에서 raw `fetch()`를 실행해도 배민이 전역 fetch를 감싸지 않고 별도
내부 API 클라이언트로 서명하기 때문에 CORS로 막힌다(상세 재현 기록은
`baemin_reviews.py`/`baemin_auth.py` 모듈 docstring 참고). 자격증명은 Fernet으로
암호화해 저장한다(`backend/app/credential_crypto.py`, `CREDENTIAL_ENCRYPTION_KEY`
환경변수). 로그인 시 배민의 자동화 탐지가 기본 headless 세션을 차단해서
(`--disable-blink-features=AutomationControlled`, `navigator.webdriver` 오버라이드,
데스크톱 Chrome User-Agent/뷰포트/로케일 위장으로 우회, 상세는 `baemin_auth.py`
모듈 docstring 참고) — 이 봇 탐지 우회는 사용자 본인 계정에 한해 명시적으로
승인받았다. 쿠팡이츠/요기요는 아직 미승인이라 "절대 금지" 그대로 유지, 리뷰
답글 실제 자동 등록도 여전히 범위 밖이다(매출·입금·재주문율 실데이터 연동은
아래 "배민 매출·입금·재주문율 연동" 절 참고). 설계 상세는
`docs/superpowers/specs/2026-08-09-baemin-review-scraping-design.md` 참고.

**배포 환경(Railway)에서의 로그인 위임 (2026-08-18/19 수정)**: 이 봇 탐지
우회는 개발 중 항상 맥북(홈 IP)에서 직접 실행하며 검증됐는데, 실제로
Railway에 배포한 뒤 처음 "가게 연결" 로그인을 시도해보니 Railway의 클라우드
IP에서는 로그인 폼 자체가 렌더링되지 않고 `get_by_test_id("id")`의 fill()이
30초 타임아웃으로 막히는 현상이 실측 확인됐다 — 위 우회 설정을 그대로
써도 소용없고, 처음부터 이 로그인은 반드시 홈 IP에서 실행돼야 한다.
그래서 `baemin_login`을 직접 호출하는 세 곳 전부 `CRAWL_WORKER_URL`이
설정된 배포 환경에서는 크롤 워커(맥북)에 위임하도록 바꿨다: (1)
`ads.py`의 입찰가 반영(`/internal/apply-bid`), (2) `store_connections.py`의
최초 로그인(`/internal/baemin-login` — 로그인+매장목록조회만 위임하고
자격증명 암호화·DB 저장은 계속 Railway가 담당), (3) `store_connections.py`의
데이터 동기화 시작(`/internal/sync-reviews` — `run_review_sync_job`은
자체 세션으로 Railway와 동일한 Postgres에 쓰므로 프론트 폴링은 그대로
동작). `CRAWL_WORKER_URL`이 없으면(로컬 개발) 기존처럼 이 프로세스에서
직접 로그인한다. 이 발견 과정에서 `crawler/.env.worker`에
`CREDENTIAL_ENCRYPTION_KEY`가 누락돼 있던 것도 같이 발견해 추가했다(배민
자격증명 복호화가 이 세 경로 전부와 `_run_local_crawl`에도 필요한 값).

### 배민 매출·입금·재주문율 연동 (예외 허용)
원래 "배민의 주문/정산 실데이터 연동은 여전히 범위 밖"이었으나, 리뷰 연동에
이어 실 SaaS 전환 로드맵 3번의 다음 단계로 대시보드의 매출/입금/재주문율도
실제 배민 데이터로 교체하기로 결정했다(2026-08-11). 사장님광장의 "가게통계"
화면(`GET /v3/statistics/orders/summary`로 매장별 일별 매출, `GET
/v3/dashboard/crmInfo`로 매장별 일별 신규/재주문 건수)과 "정산내역" 화면
(`GET /v3/settle/history/summary`)의 organic 응답을 리뷰와 동일한 방식으로
가로챈다. 여기에 더해 "주문내역" 화면의 `GET /v4/orders` 응답도 함께
가로챈다 — 가게통계의 월 선택기는 완료된 3개월만 제공하고 진행 중인
이번 달은 구조적으로 선택지에 없어서(Task 2 실측 확인), 이번 달 진행분
매출은 주문내역 화면에서 별도로 보완한다. 이 절의 범위에서는 개별 주문을
`orders` 테이블에 행으로 저장하지 않고 날짜별 합계만 `daily_settlements`에
upsert했다 — 이후 개별 주문 저장도 실제로 연동됐다(2026-08-13, 아래 "배민
주문내역(개별 주문) 연동" 절 참고). 지금은 같은 `GET /v4/orders` 응답을 두
용도로 함께 쓴다: 날짜별 합계는 여기, 개별 주문 행은 그 절. 조사 결과 입금은 매장(브랜드)별 필터가
API에 없어 계정(사업자) 전체 합산으로만 나오는 것을 확인했고, 그래서
매출·재주문율도 브랜드별로 나누지 않고 계정 전체 합산 하나로만 저장하기로
결정했다 — `daily_settlements`/`repurchase_metrics` 스키마 변경 없이 기존
구조를 그대로 upsert 대상으로 쓴다. 매출/입금은 이번 달 포함 최근 3개월을
백필하고(가게통계로 완료된 3개월, 주문내역으로 이번 달 진행분), 재주문율은
배민 API 자체가 고정 최근 7일 창만 줘서 소급이 안 되며 동기화할 때마다 최근
7일 스냅샷만 갱신된다. "가게 연결" 화면의 버튼은 "리뷰 동기화"에서 "데이터
동기화"로 이름을 바꿨다 — 같은 로그인 세션 안에서 리뷰와 매출/입금/재주문율을
한 번에 가져온다. 쿠팡이츠/요기요는 아직 미승인이라 "절대 금지" 그대로
유지. 설계 상세는
`docs/superpowers/specs/2026-08-11-baemin-sales-deposit-repurchase-design.md`
참고.

### 배민 데이터 동기화 증분 조회 (예외 허용 아님 — 순수 성능 개선)
원래 "데이터 동기화"는 리뷰/월별매출(가게통계)/입금(정산내역)/정산상세
(카드클릭)/우리가게클릭 5개 소스를 매 실행마다 전부 다시 긁었다(개별
주문내역만 유일하게 증분). 실제 배포 환경 첫 실측(2026-08-19, 데모 계정)
에서 22분이 걸린 걸 확인하고, orders가 이미 쓰는 "DB에서 커서를 계산해
그 이후만 조회" 패턴을 나머지 5개에도 확장했다. 새 컬럼/테이블은 없다 —
매번 `MAX(날짜)`/해당 컬럼 `IS NOT NULL` 존재 여부로 커서를 그때그때
계산한다(정규화 원칙 유지). 리뷰는 도착 순서(최신순) 기준 이미 아는
`external_review_id`가 연속 5개 나오면 "더보기" 클릭을 멈춘다
(`_consecutive_known_count`, `backend/scrapers/baemin_reviews.py`).
월별 매출·우가클은 `filter_months_needing_sync`로 이미 데이터가 있는
달을 건너뛰되, 재주문율(crmInfo)은 날짜 소급이 안 되는 "최근 7일" 고정
스냅샷이라 매출을 전부 건너뛰어도 매번 최소 1개월은 방문해서 갱신하고,
우가클의 진행 중인 이번 달은 항상 재조회 대상에 포함한다. 입금/정산
상세는 `compute_settlement_sync_range`(orders의 `compute_order_sync_range`와
동일한 커서-2일 패턴)로 조회 폭을 좁힌다. 최초 동기화(커서/기존 행 없음)
동작은 전부 그대로다 — 이 변경은 두 번째 이후 동기화만 빠르게 만든다.
버튼을 안 눌러도 자동으로 도는 백그라운드 스케줄러는 별도 설계 대상으로
아직 범위 밖이다. 설계 상세는
`docs/superpowers/specs/2026-08-19-baemin-sync-incremental-fetch-design.md`
참고.

### 배민 데이터 자동 동기화 스케줄러 (예외 허용 아님 — 순수 편의 기능)
원래 데이터 동기화는 "가게 연결" 화면의 "데이터 동기화" 버튼을 사용자가
직접 눌러야만 실행됐으나(2026-08-19 증분 조회 절 참고), 버튼을 누르지
않아도 매일 자동으로 최신 데이터가 갱신되도록 스케줄러를 추가했다
(2026-08-20). Railway 백엔드 FastAPI `lifespan`(`backend/app/main.py`)이
기동 시 `asyncio` 무한 루프(`backend/app/scheduler.py`의
`run_scheduler_loop`)를 하나 띄우고, 이 루프는 KST 04:00마다 배민 실계정이
연결된(`credential_ciphertext IS NOT NULL`) 모든 매장을 순차적으로
동기화 디스패치한다 — 새 외부 의존성(APScheduler 등) 없이 `asyncio.sleep`
하나로 구현했다. 잡 생성·워커 위임 로직은 수동 버튼과 완전히 같은 함수
(`store_connections._dispatch_sync_job`)를 공유하고,
`review_sync_jobs.triggered_by`(`manual`/`scheduled`) 컬럼으로 어느
경로로 만들어진 잡인지 구분한다. 워커(맥북)가 꺼져있거나 응답이 없으면
그 매장의 잡만 실패로 기록되고 다음 날 04시에 다시 시도된다 — 서버가
죽거나 스케줄러 자체가 멈추지 않는다. Railway 백엔드는 항상 단일
인스턴스로만 뜨므로(`backend/railway.json`에 `numReplicas` 미지정) 같은
프로세스 안에서 스케줄러가 중복으로 도는 상황은 없지만, 최종 리뷰에서
프로세스 경계를 넘는 중복 실행 경로가 하나 더 발견됐다(2026-08-20) —
`crawler/start_worker_services.sh`가 맥북에서 띄우는 크롤 워커도 정확히
같은 `app.main:app`을 같은 운영 Postgres에 대고 그대로 실행하는데, 그
프로세스는 `CRAWL_WORKER_URL`이 없어 스케줄러가 무조건 켜져 있었다면
"워커 없는 로컬" 분기를 타고 자기 자신이 직접 동기화를 돌려버린다 —
그러면 Railway와 워커 두 프로세스가 같은 KST 04:00 순간에 같은 매장·같은
배민 계정을 향해 각자 독립적으로 동기화를 실행하는, 이 기능이 애초에
막으려던 바로 그 이중 실행 사고가 재발한다. 그래서 스케줄러는
`ENABLE_SYNC_SCHEDULER=true`가 명시적으로 설정된 프로세스에서만
켜지도록 opt-in으로 만들었다(`backend/app/main.py`) — 이 값은 Railway
백엔드 서비스에만 설정하고 `crawler/.env.worker`에는 절대 설정하지
않는다. "가게 연결" 화면은 배민 카드마다 마지막 동기화가 수동/자동 중
무엇이었는지, 언제, 성공했는지를 `GET /store-connections`의 `last_sync`
필드로 보여준다. 쿠팡이츠/요기요는 여전히 Mock이라 이 스케줄러의 대상이
아니다. 설계 상세는
`docs/superpowers/specs/2026-08-20-baemin-auto-sync-scheduler-design.md`
참고.

### LLM 기반 답글 생성 (RAG, 예외 허용)
원래 답글 생성은 `reply_styles`(4개 페르소나) 고정 템플릿에 문자열
치환만 하는 Mock이었으나(CLAUDE.md "절대 금지"의 "실제 AI API 호출
금지" 원칙), 실 SaaS 전환 로드맵 4번으로 실제 Claude API 호출을 처음
도입했다(2026-08-21 승인, 실비용 발생 인지). 리뷰가 배민에서
동기화되는 시점(`review_sync.py`)에 Haiku로 불만 유형(`category`:
food_quality/delivery/hygiene/service/price/missing_or_wrong_item/
no_issue)과 민감도(`is_sensitive`), 별점-텍스트 불일치
(`sentiment_conflict`)를 분류해 `reviews`에 저장한다 — 답글 생성
버튼을 누르기 전에도 민감 리뷰 알림(`alerts`, `sensitive_review`
타입)이 뜨게 하기 위해서다. 원래 `category="no_issue"`(불만 신호 없음)인
긍정 리뷰는 기존 4-페르소나 템플릿 경로를 그대로 쓰고, 그 외 문제
리뷰만 새 RAG 경로(`backend/app/llm/`)를 탔으나, 2026-08-24부터 no_issue를
포함한 모든 리뷰가 RAG 경로를 탄다(아래 "no_issue 리뷰도 RAG로 통합" 절
참고) — 이 가게의 진짜 답글 사례(`golden_examples`, `category` 필터로만
검색하고 벡터 DB는 쓰지 않는다)와 매장별 스타일 규칙(`store_style_profile`,
Sonnet이 5~7줄로 요약해 캐싱)을 few-shot으로 반영해 Sonnet이 생성한다. few-shot
프롬프트에는 "스타일만 참고, 사건 내용 복사 금지" 지시를 반드시
포함한다(소량 예시의 과적합 방지). 사장님이 AI 초안을 수정하거나
초안 없이 직접 써서 저장하면 그 답글이 자동으로 새 골든 예시로
승격되고(`is_manual=true`), 스타일 프로파일이 재생성된다 — 단
`store_style_profile` 재생성은 반드시 `is_manual=true AND
is_synthetic=false` 데이터로만 하며, 순수 AI 생성 모범답안을 학습
소스로 쓰지 않는다(자기 산출물을 자기가 학습하는 순환 오염 방지).
브레인스토밍 중 프로덕션 데이터를 실측 확인해, 기존 답글 700여 건은
사장님이 실제 사용 중인 별도 AI 도구 + 직접 작성 결과였고(seed Mock
아님), 그중 별점 1~2점 답글 5건은 전부 사장님이 직접 썼다고 확인받아
`backend/scripts/backfill_golden_examples.py`로 골든 예시에 백필했다.
데이터가 적을 때 AI로 예시를 증강하는 방식("메아리 증폭" — 편향만
증폭되고 정보량은 그대로)은 명시적으로 채택하지 않았다 — 대신 사장님
온보딩으로 진짜 예시를 늘리는 별도 계획
(`docs/superpowers/specs/2026-08-21-llm-rag-reply-design.md`의
온보딩 절)을 이어서 진행한다. `ANTHROPIC_API_KEY`는 Claude Pro 구독과
무관한 별도 과금 API 키다(console.anthropic.com 발급). 설계 상세는
`docs/superpowers/specs/2026-08-21-llm-rag-reply-design.md` 참고.

#### no_issue 리뷰도 RAG로 통합 (2026-08-24)
원래 `category="no_issue"`(칭찬/무난) 리뷰는 무료 4-페르소나 템플릿
치환만 쓰고 RAG를 타지 않았으나, 실사용 중 별점은 높아도 구체적 피드백이
담긴 리뷰(예: "기본맛으로 주문했는데 맵지 않아요~ 조금 더 매우면 맛있을
것 같아요", 4점)에 리뷰 내용과 전혀 무관한 정형 문구("맛있게 드셨다니
다행입니다")가 그대로 붙는 문제가 확인됐다 — 분류 프롬프트가 "명백한
불만"이 없으면 전부 no_issue로 묶기 때문에, 템플릿 경로는 이런 리뷰의
실제 내용을 반영할 방법이 구조적으로 없었다. 사장님이 RAG를 도입한
목적 자체가 "진짜 내 말투가 반영된 AI 답글"이었는데, 절대다수(실측
확인 결과 매장 리뷰 756건 중 749건)인 no_issue 리뷰가 전부 페르소나
템플릿만 타면 그 차별점이 드러나지 않는다는 지적도 있었다. 그래서
category 분기(`backend/app/routers/reviews.py`의 `generate_reply`)를
없애고 모든 리뷰가 `generate_ai_reply`(RAG)를 타도록 통합했다.
`reply_styles.template_high/mid/low` 컬럼은 스키마에 남아있지만(되돌릴
여지, DROP 안 함) 이제 아무 코드에서도 읽지 않는다 — `_band`/
`_fill_template` 함수는 삭제했다.

no_issue 리뷰의 RAG 프롬프트(`backend/app/llm/generate.py`의
`_build_user_message`)는 "불만 유형: no_issue"처럼 없는 불만을 있는
것처럼 프레이밍하지 않는다 — 대신 "특이 불만 없음(칭찬 또는 중립적인
리뷰), 구체적 취향/요청이 있으면 반영"으로 안내하고, 문제 리뷰 전용인
"최근 30일 반복 불만 건수" 안내도 no_issue에는 붙이지 않는다. 골든 예시
승격 가드(`save_final_reply`의 `if review.category != "no_issue"`)도
없앴다 — no_issue도 few-shot 예시가 쌓여야 실제 학습된 말투가
반영되므로, 사장님이 no_issue 리뷰에 직접 쓰거나 수정한 답글도 이제
`golden_examples`로 승격된다.

이 변경으로 답글 생성 API 호출이 실질적으로 전부 유료 Sonnet 호출이
된다(기존에는 no_issue 비중이 커서 상당수가 무료 템플릿이었음) — 실비용
증가를 인지하고 진행했다. 별도 조사에서, 이 분류 기능 자체가 2026-08-21에
처음 배포됐고 그 이전에 이미 동기화된 과거 리뷰는 증분 동기화 특성상
재분류되지 않는다는 것도 확인했다(예: 명백한 1점 불만 리뷰가 옛날에
동기화됐다는 이유만으로 no_issue로 방치됨) — 이 재분류 백필은 아직
별도로 진행하지 않았고 향후 스코프로 남아있다.

`reply_styles`에 5번째 페르소나 "찐사장님 말투"를 추가했다(2026-08-24)
— 나머지 4개 페르소나는 여전히 "표면적 톤(이모지/격식)만 조절하는 얇은
레이어"이고, 이 5번째는 그 톤 레이어 자체를 끄는 옵션이다.
`tone_instruction`에 "페르소나에 맞춰 임의로 바꾸지 말고 사장님의 실제
말투를 그대로 따르라"고 명시해, `store_style_profile`/골든 예시 그라운딩
결과가 페르소나 이모지·격식 지시에 덮이지 않고 그대로 나오게 한다.

no_issue RAG 통합 이후 실사용 스팟체크 중, 한 배민 계정에 브랜드가
여러 개(치밥대장/블랙닭갈비/곱도리탕/행복가성비) 딸려있을 때 답글이
"안녕하세요, 치킨대장 당고점입니다"처럼 리뷰의 실제 브랜드와 무관하게
항상 대표 브랜드 이름(`Store.name`)으로 시작하는 문제가 발견됐다
(2026-08-24). `generate_ai_reply`의 시스템 프롬프트가 항상
`store.name` 하나만 참조했기 때문 — 예전 템플릿의 `{store}`
플레이스홀더도 같은 한계가 있었지만 RAG가 "안녕하세요, ~입니다"를 문장
서두에 자연스럽게 넣다 보니 더 눈에 띄었다. `_resolve_display_name`
(`backend/app/llm/generate.py`)을 추가해 `review.platform_shop_no`로
`baemin_shop_brands`를 찾아 그 리뷰의 실제 브랜드 이름을 쓰도록
고쳤다 — 매칭되는 브랜드가 없으면(연결 정보 없음, 온보딩 가상 리뷰 등)
기존처럼 `Store.name`으로 폴백한다. `baemin_shop_brands.shop_name`은
배민 드롭다운 원문("[음식배달] 블랙닭갈비 노원당고개점 / 고기·구이
14804914")이라 앞뒤를 잘라 "블랙닭갈비 노원당고개점"만 프롬프트에
쓴다.

실사용 중 치밥대장에 실제로 별점 5점·내용은 불만인 리뷰(고기가 질기고
양념이 심심하다, 치킨마요 고기양 부족)가 들어와 동기화해보니, 분류
(`category`)와 자동 답글 생성이 전부 조용히 실패한 게 확인됐다
(2026-08-25) — 원인은 `crawler/.env.worker`에 `ANTHROPIC_API_KEY`가
아예 없었던 것(`backend/app/llm/client.py`의 `os.environ
["ANTHROPIC_API_KEY"]`가 `KeyError`). `review_sync.py`가 분류 실패를
`except ClassificationError: pass`로 조용히 삼키고 기본값(`no_issue`/
`is_sensitive=False`)으로 폴백하는 구조라, 에러가 화면에 전혀 안
보이고 그냥 "이 리뷰는 특이사항 없음"처럼 보였다. Railway 백엔드
서비스에는 이 키가 이미 있었지만(위 "LLM 기반 답글 생성" 절), 로그인·
스크래핑을 위임받는 크롤 워커(맥북) 프로세스는 별도 `.env.worker`를 쓰는데
거기엔 빠져 있었던 것 — `CREDENTIAL_ENCRYPTION_KEY` 누락과 같은 종류의
실수다(위 "배포 환경(Railway)에서의 로그인 위임" 절 참고). 같은 키 값을
`crawler/.env.worker`에 추가하고 워커 백엔드 프로세스를 재시작해
해결했다(2026-08-26). 이 버그 때문에 no_issue로 잘못 방치된 리뷰의
재분류 백필은 아직 별도로 진행하지 않았다.

### 배민 자동 답글 실제 제출 (예외 허용, 5점 리뷰로 한정)
원래 "절대 금지"의 "실제 자동 답글 등록 금지"는 이 프로젝트 전체에서
유일하게 끝까지 예외를 안 받은 항목이었으나, no_issue RAG 통합 이후
답글 품질을 실사용으로 확인한 뒤 실 SaaS 서비스로 쓰기에 적합하도록
실제로 배민에 자동 제출하기로 결정했다(2026-08-25, 사용자 확인). 단,
사람이 한 번도 검토하지 않은 AI 답글이 실시간으로 공개 노출되는
기능이라 위험이 크다고 판단해 **지금은 별점 5점 리뷰로만 제한**한다 —
`reply_settings.auto_reply_min_rating`을 더 낮게 설정해도
`review_sync.py`에 하드코딩된 `_AUTO_REPLY_MIN_RATING_FLOOR = 5`를
넘지 못한다(프론트 `/reviews/rules` 화면도 5점 외 선택지를 비활성화
표시). 이 하한은 신뢰가 쌓이면 낮출 수 있는 임시 안전장치다.

동작 방식: `review_sync.py`가 새 리뷰를 저장하는 시점에(sensitive_review/
negative_review 알림과 같은 자리) `reply_settings.auto_reply_enabled`가
켜져 있고 별점이 5점이면(아래 게이트 강화 이후로는 분류 결과까지 통과해야
함) `generate_ai_reply`로 답글을 생성해
`backend/scrapers/baemin_reply_submit.py`의 `submit_reply`로 실제
배민에 제출한다. 이미 배민에 사장님 답글이 달려있는 리뷰(가져올 때
`extract_owner_reply`로 감지)는 건드리지 않는다(이중 답글 방지). 성공하면
`review_replies`에 저장하고 `review.status = "answered"`로 바꾸지만,
**golden_examples로는 승격하지 않는다** — 사람이 검토하지 않은 순수
AI 산출물을 다시 학습 예시로 쓰면 "자기 산출물을 자기가 학습하는"
순환 오염이 되기 때문(golden_examples의 `is_manual=true`는 사람이
직접 쓰거나 승인했다는 전제 — 위 "LLM 기반 답글 생성" 절 참고). 실패해도
(배민 응답 실패, 네트워크 오류 등) 그 리뷰의 동기화 자체는 되돌리지
않는다 — 리뷰는 정상 저장되고 `unanswered`로 남아 사장님이 나중에
수동으로 답글을 달 수 있다.

**`submit_reply`의 실측 근거(2026-08-25, 치밥대장, 실제 5점 리뷰 1건에
진짜 제출)**: 리뷰관리 화면의 "사장님 댓글 등록하기" 버튼을 클릭하면
인라인 textarea가 열리는데, 배민이 "{닉네임}님, "을 자동으로 미리
채워둔다 — 우리 AI 답글은 이미 자체 인사말을 포함하므로 이 프리필을
`fill()`로 완전히 덮어쓴다. "등록" 버튼을 클릭하면 페이지 자신이
organic하게 서명된 요청을 보낸다(다른 배민 스크래핑과 동일한 이유로
직접 fetch() 구성은 `x-e-request` 서명이 없어 차단됨):
`POST https://self-api.baemin.com/v1/review/shops/{shopNo}/reviews/comments`,
바디 `{"contents", "reviewId", "shopNo"}`. 200 응답을 받으면 실제로
반영된다(같은 세션에서 리뷰 카드에 "사장님" 댓글이 뜨고 "삭제"/"수정"
버튼이 생기는 것까지 화면으로 확인). 대상 리뷰 카드는 리뷰 내용이
아니라 "리뷰번호 {external_review_id}" 텍스트로 찾는다 — 서로 다른
고객이 비슷한 문구("맛있어요" 등)를 남겼을 때 오답글이 달릴 위험을
피하기 위해서다.

이 기능은 다른 배민 자동화와 마찬가지로 `review_sync.py`(로그인
포함) 전체가 실행되는 프로세스 안에서 그대로 동작한다 — 배포
환경에서는 이미 `CRAWL_WORKER_URL`이 설정돼 있으면 로그인+동기화
전체가 크롤 워커(맥북)로 위임되므로(위 "배민 리뷰 연동" 절 참고) 새
위임 경로를 따로 만들 필요가 없었다. 다만 이 말은 곧 **크롤 워커
프로세스가 떠 있어야만** 자동 답글이 실제로 나간다는 뜻이다 — Railway
백엔드 배포만으로는 부족하다.

#### 안전 게이트 강화: 별점만으로는 부족함 (2026-08-26)
원래 자동 제출 조건은 `review.rating >= _AUTO_REPLY_MIN_RATING_FLOOR`
(별점 5점) 하나뿐이었으나, 실사용 중 별점 5점인데 내용은 명백한 불만인
리뷰(id 799, "고기가 질기고 뻑뻑하다/양념이 심심하다/치킨마요 고기양
부족", 단골 고객)가 실제로 들어오는 걸 확인하면서(위 "배민 매출·입금·
재주문율 연동" 절 이후 실사용 스트레스 테스트, 2026-08-25) 별점만으로는
"진짜 순수 긍정 리뷰"를 가려낼 수 없다는 게 드러났다. 마침 같은 세션에서
`crawler/.env.worker`에 `ANTHROPIC_API_KEY`가 없어 이 리뷰의 분류
자체가 조용히 실패해 `no_issue`로 방치됐던 별개 버그도 함께 발견했다
(위 문단 참고, 키 추가로 해결) — 키를 고친 뒤 재분류해보니 이 리뷰는
`category=food_quality, sentiment_conflict=true`로 정확히 잡혔다.

그래서 자동 제출 조건에 분류 결과를 추가했다: 별점 5점 하한에 더해
**`category == "no_issue"`이고 `is_sensitive`가 아니고
`sentiment_conflict`도 아닌** 리뷰만 자동 제출 대상이다(`review_sync.py`
동기화 루프의 `elif` 조건). 셋 중 하나라도 걸리면(불만 카테고리로
분류됐거나, 위생/안전 민감 사안이거나, 별점-내용이 어긋나거나) 자동
제출을 건너뛰고 `unanswered`로 남겨 사장님이 직접 확인하게 한다. 분류는
이미 이 리뷰가 DB에 저장되기 직전에(`classify_review`) 실행되므로 이
게이트를 위해 별도로 다시 분류하지 않는다 — 있는 값을 재사용할 뿐이다.

### 배민 정산 상세(수수료/배달비/고객할인/우가클비용) 연동 (예외 허용)
원래 "매출 분석" 카드의 배민 행은 `platforms.default_commission_rate`(요율)
기반 추정치(중개수수료·결제수수료)만 보여줬으나, 실 SaaS 전환 로드맵 3번의
다음 단계로 배민 정산내역 화면의 실제 차감 내역을 가져와 그 추정치를
대체하기로 결정했다(2026-08-12). 매출·입금 실데이터 연동에서 "매출과
입금이 다르다"를 D+3 시차로만 설명했던 것을, 실제로 무엇 때문에 깎이는지
보여주는 단계다. 사장님광장의 "정산내역"(`/orders/billing`) 화면에서 각
정산 배치 카드를 클릭할 때 발생하는 `GET
/v3/settle/history/details/{giveId}` organic 응답을 리뷰·매출과 동일한
방식(`page.on("response")`)으로 가로챈다(`fetch_settlement_breakdown_details`).
이 응답의 `baemin1Details`/`baeminDetails`/`cpcDetails` 블록에서 수수료
(중개이용료+결제수수료), 배달비, 고객 즉시할인, 우가클(CPC) 광고비 네
카테고리를 계산해(`map_settlement_breakdown_by_date`) `daily_settlements`에
신규 컬럼 4개(`commission_amount`/`delivery_fee_amount`/
`customer_discount_amount`/`ad_cost_amount`)로 upsert한다. 네 컬럼 모두
nullable이다 — 요기요/쿠팡이츠 행(실측 안 함)과, 아직 정산 상세 동기화가
안 된 배민 과거 날짜(백필 범위 밖) 둘 다 NULL로 남겨 "데이터 없음"과
"차감액 0원"을 구분한다. 기존 `deposit_amount`(입금액) 조회는 90일 창을
그대로 유지하지만, 정산 상세는 카드 클릭 비용이 커서 별도로 최근 30일
창만 수집한다 — 대시보드 기본 조회 기간(오늘/1주/1개월/이번달)이 전부
30일 안에 들어와 실용상 충분하다고 판단해 범위를 좁혔다. "기타"
(misc_amount) 항목은 컬럼을 두지 않고 `GET /sales/breakdown` 조회
시점에 `sales_amount − 4개 실측 카테고리 − deposit_amount`로 잔차 계산한다
(정규화 원칙) — 매출액과 입금액이 서로 다른 배민 화면에서 독립적으로 오는
값이라 완전히 안 맞을 수 있는데, 그 오차까지 "기타"로 그대로 드러내는 걸
의도된 동작으로 판단했다(음수가 나올 수도 있다 — UI는 부호에 따라 표시를
분기한다). `/sales/breakdown` 응답은 신규 컬럼이 채워진 기간이면
`is_estimate: false`와 함께 5개 실측값(수수료/배달비/고객할인/우가클비용/
기타)을 반환하고, 아직 없으면 기존처럼 `is_estimate: true`와 요율 기반
추정치로 폴백한다 — 이 분기는 배민 행에만 적용되고 요기요/쿠팡이츠는
계속 추정치다. 설계 상세는
`docs/superpowers/specs/2026-08-12-baemin-settlement-fee-breakdown-design.md`
참고.

### 배민 주문내역(개별 주문) 연동 (예외 허용)
원래 "주문내역"(`/orders`) 화면은 `seed.sql`이 만든 랜덤 Mock 500건을 그대로
보여줬고, 매출·입금 연동 때도 "개별 주문 행 저장은 범위 밖"으로 남겨뒀으나,
실 SaaS 전환 로드맵 3번의 다음 단계로 개별 주문까지 실데이터로 교체하기로
결정했다(2026-08-13). 새 엔드포인트를 만들지 않고, 이미 "이번 달 매출 보완"
용도로 가로채고 있던 주문내역 화면의 `GET /v4/orders` organic 응답을 그대로
재사용한다 — 지금까지는 날짜별 합계만 쓰고 버리던 `contents[].order`의
`orderNumber`/`orderDateTime`/`payAmount`/`itemsSummary`/`deliveryType`를
`orders` 테이블에 행으로 저장한다(스키마 변경 없음, `order_no` 기준 upsert).
`deliveryType`은 실측 확인된 `DELIVERY`/`TAKEOUT` 두 값만 매핑하고 모르는
값이 나오면 그 주문 하나만 건너뛴다. `shopNumbers`가 빈 채로 나가 계정의
모든 브랜드 주문이 한 번에 오므로 브랜드별 반복 호출이 없다.

정산 상세와 달리 **증분 동기화**다 — 개별 주문은 한 페이지에 10건씩만 와서
3개월이면 페이지네이션 클릭이 155번 필요할 만큼(실측 1,541건) 비싸기
때문이다. 동기화할 때마다 이 매장·배민의 `MAX(ordered_at)`을 커서로 삼아,
없으면(최초 실행 또는 Mock 정리 직후) 최근 3개월을 백필하고, 있으면 그
시각 −2일부터 오늘까지만 다시 조회한다(주문 상태가 뒤늦게 확정되는 경우
대비, `order_no` upsert라 겹쳐도 중복되지 않는다). 기존 Mock 배민 주문
500건 정리는 매 동기화가 아니라 배포 시 한 번만 수동 SQL로 처리한다 —
"매번 지우고 다시 채우는" 리셋 패턴은 증분 로직과 정면으로 충돌하고, Mock을
지우고 나면 커서가 비어 3개월 백필 분기가 자동으로 도는 게 자연스럽다.

이 증분 설계 때문에 **부분 수집을 절대 성공으로 취급하면 안 된다**(2026-08-13
최종 리뷰에서 실제 유실 사고로 확인). 목록이 최신순이라 중간에 끊긴 수집은
항상 가장 오래된 구간을 버리는데, 그대로 저장하면 커서만 오늘로 전진해
버려진 구간을 이후 어떤 동기화도 다시 보지 않는 영구 유실이 된다. 그래서
`fetch_orders`는 (1) 응답 URL의 `startDate`/`endDate`가 요청한 범위와 정확히
일치하는 응답만 수집하고(화면 기본 필터인 최근 7일 응답이 섞여 들어와
"요청한 3개월"인 척하는 걸 막는다), (2) 응답 본문의 `totalSize`와 실제 수집
건수를 대조해 모자라면 `BaeminStatsScrapeError`를 던진다(정산 상세의 부분
캡처 하드 에러와 같은 패턴). 날짜 범위 캘린더도 두 번 클릭하면 범위가
잡힌다는 가정이 틀려서(진입 시점 선택 상태에 따라 엉뚱한 범위나 하루짜리
단일 날짜가 적용된다) 적용 결과를 읽어 대조하고 재시도한다. 실패는 조용히
묻히지 않고 그 소스만 실패로 기록되며, 커서가 그대로 남아 다음 동기화가
같은 구간을 다시 시도한다. 설계 상세는
`docs/superpowers/specs/2026-08-13-baemin-order-history-design.md` 참고.

### 배민 우리가게클릭(우가클) 브랜드별 실데이터 연동 (예외 허용)
원래 "우가클 점수"는 카테고리 기반 `ad_campaigns`/`ad_performance_metrics`의
Mock 데이터로만 계산됐으나, 실 SaaS 전환 로드맵 3번의 다음 단계로 브랜드별
실데이터를 연동하기로 결정했다(2026-08-12). 사장님광장의 "광고·서비스관리
→ 우리가게클릭 → 마케팅 성과" 화면(`GET
/v2/statistics/campaign/cpc/metrics/{shopNumber}`)의 organic 응답을 리뷰·
매출과 동일한 방식으로 브랜드(shop_no)별로 가로챈다. 이 화면은 매출/입금과
달리 브랜드 단위로만 조회되고 계정 전체 통합 화면이 없어서, 계정 전체
합산이 아니라 **브랜드별로 완전히 분리해서** 저장하기로 결정했다 — 새 테이블
`brand_ad_click_metrics`를 추가했다(기존 `ad_campaigns`/`ad_performance_metrics`/
`ad_rank_snapshots`, 카테고리 기반 "광고 순위 모니터링"은 전혀 건드리지
않고 완전히 별개로 남아있다). 계산값(CPC/CVR/AOV/ACoS/점수)은 저장하지
않고 `acos.py`가 조회 시 실제 공식으로 계산하는 기존 정규화 원칙을
그대로 따른다. 백필은 이번 달 포함 최근 3개월(매출/입금과 동일한 폭),
"우가클 주문 비중"(전체 주문 대비 광고 경유 비중)은 분모가 브랜드별로 안
나뉘어 왜곡되므로 범위 밖으로 뺐다. "가게 연결" 화면의 "데이터 동기화"
버튼이 리뷰·매출·입금·재주문율에 이어 브랜드별 우가클 성과까지 한 번에
가져온다. "절대 금지"의 "실제 CPC 자동 입찰 금지" 원칙은 그대로 유효—
이번은 성과 조회(읽기)만이고 캠페인 설정을 바꾸는 기능은 아니다. 설계
상세는
`docs/superpowers/specs/2026-08-12-baemin-brand-ad-click-performance-design.md`
참고.

### RAG 메뉴 그라운딩 (예외 허용 아님 — 순수 정확도 개선, RAG 데이터 확장)
원래 RAG 답글 생성(`backend/app/llm/generate.py`)은 리뷰 텍스트와 사장님
말투(store_style_profile/golden_examples)만 참고했고, 이 가게가 실제로
무엇을 파는지(메뉴 구성·재료·원산지)는 전혀 몰랐다. 실사용 중 별점 5점
"치킨마요는 밥만 많고 고기가 없어요"라는 리뷰에 사장님이 직접 답글을
쓰면서도 "새로 나온 메뉴라..."처럼 틀린 추측을 하는 걸 보고(2026-08-26,
실제 리뷰 대응 중 발견) 원인을 보니, RAG가 참고할 "가게/메뉴 사실" 데이터
자체가 이 프로젝트에 없었다 — golden_examples/store_style_profile은
"어떻게 말하는지"만 담지 "무엇을 파는지"는 담지 않는다. 사장님이 직접
확인해준 바로는 배민 "사장님 한마디"(가게 소개)에 이미 "100% 순살
닭다리살만 씁니다" 같은 재료 사실이 적혀있어, 이걸 실제로 가져오기로
했다.

사장님광장의 메뉴관리 화면(`/shops/{shopNo}/menu-management/menu-groups`)에
`page.goto()`로 직접 진입하면(다른 스크래핑처럼 사이드바 클릭 불필요, 로그인
세션 안에서 shop_no만 바꿔 바로 이동 가능함을 실측 확인) 두 organic 응답이
발생한다: `GET /gateway/menu/v1/shops/{shopNo}`(가게소개/원산지/메뉴소개
텍스트)와 `GET .../shop-owners/{ownerId}/menupans/{menupanId}`(메뉴별
이름/설명/실제 구성/가격). 이 둘을 리뷰·매출과 동일한 방식(`page.on
("response")`)으로 가로채 새 테이블 `brand_menu_info`에 브랜드(shop_no)별로
저장한다(`backend/scrapers/baemin_menu.py`). "가게 연결" 화면의 "데이터
동기화"에 자동 포함되며, 메뉴는 거의 안 바뀌는 데이터라 30일 이내 이미
동기화됐으면 재조회하지 않는다(`menu_info_needs_refresh`, 카드 클릭 비용
낭비 방지 — 정산 상세와 동일한 이유).

`generate_ai_reply`는 리뷰의 `menu_summary`를 `brand_menu_info.menu_items`와
느슨하게(태그·콜론 정리 후 부분 일치) 매칭해 찾은 메뉴의 실제 구성/설명과
가게소개·원산지·메뉴소개 전문을 시스템 프롬프트에 "사실 근거용"으로
주입한다 — "리뷰가 특정 메뉴/재료를 언급하면 이 정보를 근거로 삼고, 실제
구성과 다른 원인은 추측해서 쓰지 마라"고 명시한다. 매칭되는 메뉴가 없거나
연결 정보가 아직 없으면 이 섹션 자체를 생략한다(있으면 좋은 보강 정보지
필수 전제가 아님). "절대 금지"/기존 "예외 허용" 목록에 새로 여는 항목이
없다 — 이미 리뷰·매출 스크래핑에 쓰던 것과 동일한 organic-response 가로채기
패턴을 메뉴관리 화면 하나에 추가로 적용한 것뿐이라 별도 승인 절차 없이
진행했다.

이 작업 중 별개로 사용자가 SQL(category exact match) 기반 골든 예시 검색이
"정형화된 답변"을 만든다며 벡터 DB 전환을 제안했으나(2026-08-26), 검토
결과 원인은 검색 방식이 아니라 `fetch_golden_examples`가 카테고리당 3개만
반환해 같은 카테고리 리뷰마다 거의 같은 예시가 반복 주입되는 것으로 진단—
벡터 DB 전환은 보류하고 이 메뉴 그라운딩부터 마무리하기로 확인받았다. 벡터
DB 여부는 재검토 대상으로 남겼고, 실제로 메뉴 그라운딩 마무리 직후 같은
날 다시 진행하기로 결정했다(아래 "골든 예시 벡터 검색" 절 참고).

### 골든 예시 벡터 검색 (예외 허용, "벡터 DB 미사용" 원칙 번복)
원래 golden_examples 검색은 "검색은 category 필터만 쓴다(벡터 DB
미사용)"는 명시적 설계 결정이었으나(위 "LLM 기반 답글 생성" 절), 사용자가
다시 요청해 실제로 도입했다(2026-08-26). Anthropic은 임베딩을 API로
제공하지 않아 별도 벤더가 필요했는데, Voyage AI(`voyage-4` 모델,
1024차원)를 선택했다 — Anthropic이 공식 추천하는 임베딩 파트너이고,
계정당 2억 토큰이 무료라 이 프로젝트 규모(골든 예시 수백 건)에서는
사실상 비용이 들지 않는다(`VOYAGE_API_KEY` 환경변수, voyageai.com에서
발급, `backend/app/llm/embedding.py`).

**처음엔 pgvector 없이 구현했다가 사용자 지적으로 다시 만들었다**
(2026-08-26). store당 골든 예시가 많아야 수백 건이라는 이유로 별도 벡터
확장 없이 `embedding DOUBLE PRECISION[]` 컬럼 + 애플리케이션 레벨 코사인
유사도(선형 스캔)로 1차 구현했는데, 사용자가 "pgvector까지 구현해야
벡터 DB가 완성되지 왜 멋대로 판단하냐"고 지적했다 — 맞는 지적이었다.
"벡터 DB 구축"을 명시적으로 요청받았는데 그 핵심(실제 벡터 데이터베이스
기술)을 임의로 생략하고 사후에 설명하는 방식으로 진행한 것 자체가
잘못이었다. 다시 확인해 **완전히 pgvector로 전환**했다 — `golden_examples.
embedding`은 실제 `vector(1024)` 타입이고, 순위는 `ORDER BY embedding <->
:query`로 Postgres가 SQL 레벨에서 직접 계산한다(SQLAlchemy에서는
pgvector-python의 `.cosine_distance()` 컴패리터, `app/llm/rag.py`).
Railway 프로덕션 Postgres는 pgvector 0.8.6이 이미 설치돼 있었지만, 로컬
개발 DB(Docker 컨테이너 `baemin-verify-db2`, 원래 plain `postgres:16`
이미지)는 확장 자체가 없어서 `pgvector/pgvector:pg16` 이미지로 교체했다
(`pg_dump`/`pg_restore`로 기존 로컬 데이터 보존, 컨테이너 이름/포트 15432는
그대로 유지). category 필터는 그대로 유지한다(정밀도 보존 — 배달 불만이
위생 불만 예시를 끌어오면 안 되므로) — 그 안에서만 순위를 최신순 대신
리뷰 내용과의 의미적 유사도로 매긴다. 이렇게 카테고리당 예시 풀이
3개보다 많으면, 리뷰 내용에 따라 실제로 다른 예시가 뽑히게 된다(원래
진단한 "정형화" 원인 해결 — 실측: food_quality 카테고리 예시 6개 중
"고기가 질기고 양이 적었다"는 쿼리엔 대창 질김 리뷰가, "숯불향이
안 난다"는 쿼리엔 삶은 맛 관련 리뷰들이 서로 다르게 1순위로 뽑힘).

pgvector는 SQLite에 없는 Postgres 전용 확장이라, 이 프로젝트의 나머지
전체 테스트가 쓰는 in-memory SQLite로는 `ORDER BY <->` 실행 자체를
검증할 수 없다 — `embedding` 컬럼은 `Vector(1024).with_variant(JSON
(none_as_null=True), "sqlite")`로 타입만 SQLite에서도 테이블 생성이
되게 맞추고(그 variant로는 `<->` 연산자를 못 씀), 실제 순위 계산 검증은
로컬 Postgres(pgvector 설치됨)를 쓰는 `tests/test_llm_rag_pgvector.py`
하나만 별도로 한다(로컬에 Postgres가 없는 환경, 예: CI에서는 자동
스킵). 이 프로젝트에서 유일하게 "실행하려면 실 Postgres가 있어야 하는"
테스트 파일이다.

`embedding`이 없는 행(백필 전, 또는 Voyage 호출 실패로 저장 시점에 못
채운 행)은 배제하지 않고 유사도 순위 뒤에 최신순으로 붙는다 — 부분
백필 상태에서도 기존과 동일하게 동작한다. Voyage 호출 자체가 실패하면
(키 미설정, API 장애 등) 전체를 기존처럼 최신순 폴백으로 돌린다(답글
생성이 임베딩 API 가용성에 발목잡히면 안 된다는, 이 프로젝트의 다른
LLM 폴백들과 동일한 원칙). golden_examples를 만드는 4곳(사장님이 직접
저장하는 `save_final_reply`, 답글 온보딩 `answer_scenario`, 백필
스크립트 2개) 전부 생성 시점에 `review_text`를 벡터화해 저장한다 —
요청 경로(save_final_reply/answer_scenario)는 FastAPI `BackgroundTasks`로
응답 이후에 계산해 Voyage 호출 지연이 저장 요청 자체를 느리게 만들지
않는다(`refresh_store_style_profile_background`와 동일한 패턴, 자체
`SessionLocal` 사용). 기존에 이미 쌓여있던 골든 예시(실측 28건, 앞선
"760여 건"은 리뷰 테이블 건수와 착각한 오기)는
`backend/scripts/backfill_golden_example_embeddings.py`로 한 번에
채웠다 — 여러 번 실행해도 안전하다(`embedding IS NULL`인 행만 대상).
review_text가 빈 문자열인 행(별점만 있고 내용 없는 리뷰)은 건너뛴다 —
Voyage가 배치 안에 빈 문자열이 하나라도 있으면 요청 전체를 400으로
거부한다(실측 확인).

이 작업 중 SQLite JSON 타입의 별개 버그도 발견해 고쳤다 — `none_as_null`
기본값(False)이면 Python `None`이 SQL `NULL`이 아니라 JSON 리터럴
"null"(텍스트)로 저장돼, `embedding.is_(None)` 같은 `IS NULL` 조회가
전혀 매칭되지 않았다(백필 스크립트 테스트에서 실측 확인).

### 모바일 앱 (예외 허용)
원래 "Flutter 앱 구현 금지"로 모바일 앱 자체를 범위 밖으로 뒀으나, 웹과 같은
백엔드를 쓰는 React Native 앱을 추가하기로 결정했다(추후 결정으로 예외 허용 —
과제 범위 확장). 기술 스택은 Flutter가 아니라 React Native이고, 새 백엔드
엔드포인트를 만들지 않고 기존 FastAPI Mock API를 그대로 재사용한다 — 즉 이
예외도 "새로운 실제 연동을 추가한다"가 아니라 "이미 있는 Mock 데이터를 다른
클라이언트(앱)로도 보여준다"는 범위다. 복잡한 권한/다중 사업자 권한 관리는
여전히 금지 — 웹과 동일하게 사장 1명 = 로그인 1개 기준으로 만든다.

## 개인정보 원칙
- 실제 개인정보는 저장하지 않는다.
- 전화번호는 원문 대신 phone_hash로만 저장한다.
- 사업자번호, 스토어 아이디, 주문번호는 전부 Mock 값이다.
- 주민번호와 실명은 받지 않는다 (법적 근거 없음).
- users 테이블 컬럼은 최소화: id, email, nickname, phone_hash,
  marketing_agreed, created_at.

## DB 설계 (24개 테이블)
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, golden_examples, store_style_profile, review_replies,
reply_styles, reply_settings, daily_settlements, repurchase_metrics,
ad_campaigns, ad_performance_metrics, ad_rank_snapshots, alerts,
social_accounts, signup_verifications, review_sync_jobs,
baemin_shop_brands, brand_ad_click_metrics, payments.

### 테이블 용도
- users: 사장 계정. 전화번호는 phone_hash로 비식별화.
- stores: 매장 정보.
- platforms: 배달 플랫폼 마스터(배민, 쿠팡이츠 등). 로고/수수료율 확장 대비 테이블화.
- store_platform_connections: 매장과 플랫폼의 N:M 중간 테이블.
- subscriptions: Basic/Pro 플랜, 하루 답글 생성 한도, Pro 기능 잠금.
- orders: 주문내역(주문번호, 주문시각, 주문메뉴, 주문유형, 주문금액). 배민
  행은 실데이터다(2026-08-13, 위 "배민 주문내역(개별 주문) 연동" 절 참고) —
  요기요/쿠팡이츠 행은 계속 seed.sql의 Mock이라 "주문내역" 화면은 배민만
  필터링해서 보여준다. `ordered_at`은 TIMESTAMPTZ이고 배민이 주는 값은
  오프셋 없는 한국 벽시계 시간이라, 저장 전에 Asia/Seoul 타임존을 붙인다.
- reviews: 리뷰(별점, 내용, 고객 닉네임, 주문 횟수, 상태). store_id/platform_id/
  menu_summary를 직접 가진다 — 주문과 독립적으로 적재 가능(배민 리뷰 API에는
  주문과 연결할 공통 키가 없음). order_id는 있으면 연결하는 선택적 FK.
  image_urls(TEXT[])는 고객이 리뷰에 첨부한 사진 URL 목록이다(2026-08-24) —
  실 계정 raw JSON 확인 결과 배민 리뷰는 `images[].imageUrl` 배열을 주고,
  답글(comments)과 동일하게 `displayStatus`가 `DISPLAY`가 아닌 사진(가려짐/
  삭제)은 걸러낸다(`backend/scrapers/baemin_reviews.py`의
  `extract_image_urls`). 새 테이블을 만들지 않고 배열 컬럼으로 저장했다 —
  사진마다 별도로 다루는 로직(모더레이션, 좋아요 등)이 없어 단순 표시용
  목록이라 정규화 실익이 없다고 판단. 기존에 동기화된 리뷰는 이 컬럼이
  빈 배열로 남고, 다음 동기화부터 채워진다(소급 백필 없음).
- golden_examples: RAG few-shot 소스. 사장님이 직접 쓰거나 승인한 진짜
  답글(is_manual=true)과 예시 부족 시 보충하는 순수 AI 생성 모범답안
  (is_synthetic=true)을 함께 담는다. 검색은 category로 먼저 거르고, 그
  안에서 embedding(pgvector `vector(1024)`, Voyage AI로 계산, nullable)
  기반 코사인 거리로 Postgres가 SQL 레벨에서 직접 순위를 매긴다
  (2026-08-26, 위 "골든 예시 벡터 검색" 절 참고).
- store_style_profile: 매장별 답글 스타일 규칙(5~7줄) 캐싱. 진짜
  골든 예시로만 재생성한다.
- review_replies: AI 추천 답글 Mock과 사장 최종 답글.
- reply_styles: 답글 말투 스타일 마스터(이모지 불맛, 담백한 손맛, 다정한
  슴슴함, 위트있는 칼칼함, 찐사장님 말투).
- reply_settings: 가게별 답글 설정(홍보문구, 닉네임/메뉴/가게명 포함 여부, 부정 리뷰 홍보문구 포함 여부).
  auto_reply_enabled/auto_reply_min_rating은 실제로 배민에 자동 제출된다(2026-08-25,
  5점 리뷰로 한정 — 위 "배민 자동 답글 실제 제출" 절 참고).
- daily_settlements: 일별 매출과 입금을 함께 저장(정산 지연 반영). 배민
  행에는 정산 상세 실측 컬럼 4개(수수료/배달비/고객할인/우가클비용, 전부
  nullable)도 같이 담는다(위 "배민 정산 상세..." 절 참고).
- repurchase_metrics: 날짜별 재주문율, 신규 주문 수, 재주문 수, 보정 전/후.
- ad_campaigns: 광고 캠페인(카테고리, 현재 CPC, 목표 순위).
- ad_performance_metrics: CPC, CVR, AOV, ACoS, 광고 성과 점수.
- ad_rank_snapshots: 순위 스냅샷. 두 종류가 한 테이블에 공존한다 —
  (1) 시간별 Mock 스냅샷(distance_km NULL): 현재 순위, 경쟁 가게 예상 CPC, 상태.
  (2) 반경별 실측 스냅샷(distance_km NOT NULL): crawler/로 실제 배민 앱을
  스크롤하며 수집한 point_label/거리/순위/스캔개수/위 광고개수. 경쟁 가게
  CPC는 실측 불가능해 이 종류에는 저장하지 않는다(NULL).
- alerts: 부정 리뷰, 미답변, 순위 하락, 민감 리뷰(sensitive_review) 알림.
  민감 리뷰와 부정 리뷰(negative_review, 별점 2점 이하)는 실제로 동적
  생성되고(review_sync.py), 나머지 두 타입(미답변/순위 하락)은 여전히
  seed.sql Mock이다(실동작화는 별도 스코프). 부정 리뷰는 원래 seed.sql이
  orders.id = reviews.order_id로 조인해 만들었으나, 실 배민 리뷰는
  order_id가 항상 NULL이라 이 조인에 걸리지 않아 실 연동 이후로는 전혀
  안 만들어지고 있었다(2026-08-25 실측 확인, review_sync.py에서 직접
  생성하도록 수정).
- social_accounts: 소셜 로그인(카카오 등) 연결. provider 문자열 기반이라 확장 대비.
- signup_verifications: 이메일 회원가입 인증 코드(Resend 실발송). users를
  참조하지 않는다 — 인증이 끝난 뒤에만 계정이 생성되기 때문. purpose 컬럼은
  'phone' 값도 허용하지만 현재 코드 경로에서는 만들지 않는다(휴대폰 인증 단계를
  가입 위자드에서 뺐기 때문 — 위 "이메일 인증" 절 참고).
- review_sync_jobs: 배민 데이터 동기화 작업 상태(pending/running/success/failed).
  리뷰뿐 아니라 매출/입금/재주문율/우리가게클릭도 같은 작업 안에서 함께
  동기화한다. "가게 연결" 화면의 "데이터 동기화" 버튼 → 백그라운드 작업 →
  폴링에 쓰인다. triggered_by(manual/scheduled)로 사용자가 직접 누른
  건지 매일 04시 자동 스케줄러가 만든 건지 구분한다(위 "배민 데이터
  자동 동기화 스케줄러" 절 참고).
- baemin_shop_brands: 배민 계정 하나에 딸린 여러 브랜드(매장) 목록. 로그인
  시 발견되는 shopNo/매장명을 저장해 리뷰 관리 화면의 브랜드 선택
  드롭다운에 쓴다.
- brand_ad_click_metrics: 브랜드(shop_no)별 일별 우리가게클릭 광고 성과
  원본(노출/클릭/주문/광고비/광고매출). 계정 전체 합산이 아니라 브랜드별로
  완전히 분리 저장한다(우리가게클릭 화면 자체가 브랜드 단위로만 조회되기
  때문). ad_campaigns(카테고리 기반, 광고 순위 모니터링용)와는 별개.
- payments: 토스페이먼츠 결제 기록(테스트 키). 일회성 결제만, 정기결제 없음.

### 핵심 관계 (모든 관계에 외래키와 삭제 정책 명시)
- users 1:N stores
- stores N:M platforms (중간 테이블 store_platform_connections)
- stores 1:N orders
- orders 1:1 reviews (reviews.order_id, 선택적 FK — 현재 실제로 채워지는 경우 없음)
- reviews N:1 stores, reviews N:1 platforms (직접 참조, 주문 조인 없이 조회)
- reviews 1:N review_replies
- stores 1:N golden_examples, golden_examples는 reviews/review_replies를
  선택적으로 참조(source_review_id/source_reply_id)
- stores 1:1 store_style_profile
- stores 1:1 reply_settings, reply_settings는 reply_styles 참조
- daily_settlements는 store와 platform 참조, 매출액과 입금액에 더해 배민
  정산 상세 실측 컬럼 4개(nullable)도 함께 가진다
- ad_campaigns는 store 참조
- ad_performance_metrics와 ad_rank_snapshots는 ad_campaigns 참조
- alerts는 store 참조
- users 1:N social_accounts
- review_sync_jobs는 store, platform 참조
- baemin_shop_brands는 store_platform_connections 참조
- brand_ad_click_metrics는 store와 platform 참조 (shop_no는 FK가 아니라 값만 저장)

### 정규화 원칙
- 매출 요약은 별도 테이블로 저장하지 않는다. daily_settlements를 기간별로
  집계한다. 요약을 물리 테이블로 중복 저장하면 원본과 불일치가 생기기 때문.

## 광고비율 공식 (실제 계산할 유일한 로직)
ACoS(%) = CPC / (CVR × AOV) × 100
- CPC: 클릭당 광고비 (광고비 ÷ 클릭수)
- CVR: 전환율, 반드시 소수 0~1로 계산 (18.4%는 0.184). 절대 퍼센트값을 그대로 넣지 마라.
- AOV: 평균 객단가 (광고매출 ÷ 주문수)
- 노출수와 클릭률(CTR)은 광고비와 광고매출 양쪽에서 약분되므로 세 값만으로 계산한다.

### 점수화 가정 (추정임을 명시)
- ACoS 10% 미만: 90점 이상
- ACoS 10~15%: 80점대
- ACoS 15~25%: 70점대
- ACoS 25% 이상: 개선 필요

## 창의 기능: 광고 순위 모니터링
경쟁 가게가 CPC를 조금만 올려도 순위가 밀리는 현장 고충을 위한 기능.
ad_rank_snapshots에 카테고리, 현재 CPC, 목표 순위, 현재 순위, 경쟁 가게 예상
CPC, 상태, 추천 액션을 담는다.

ad_campaigns.shop_no(nullable)가 채워진 캠페인(현재 치밥대장=id 1만)은 실제
배민 데이터 기반으로 동작한다 — 광고 성과(GET /ads/performance)는
BrandAdClickMetric(우가클 실데이터) 집계, 현재 순위는 아래 반경별 실측
스냅샷 중 distance_km=0(가게 주소) 최신 행을 쓴다. shop_no가 없는 캠페인은
기존처럼 시간별 스냅샷을 수집됐다고 가정한 Mock으로 저장·표시한다. 경쟁
가게의 CPC는 shop_no 유무와 무관하게 배민이 아무에게도 노출하지 않아 항상
추정치이며, 화면에도 "(추정)"으로 표시한다.

### 반경별 실측 순위 (crawler/, 예외적으로 실제 크롤링 허용)
가게 기준 거리(0km / 1.5~2.5km / 2.5~3.5km)에 따라 카테고리 내 순위가 얼마나
달라지는지 실측으로 보여주는 기능. 원래 "실제 광고 순위 크롤링 금지"
원칙이었으나, crawler/ 하위의 Appium 실기기 자동화를 실측 검증(스크롤 폭·
캡처 타이밍 튜닝 포함)한 뒤 이 기능에 한해 예외를 허용하기로 결정했다.

- crawler/run_crawl.py: 실기기에서 배민 앱을 조작해 GPS를 반경별로 이동시키며
  카테고리 리스트를 스크롤·캡처해 목표 가게의 순위를 찾는다. output/results.csv로
  저장한다. 사이트(FastAPI/Next.js)와는 별개 프로세스지만, shop_no가 있는
  캠페인은 화면의 "우리가게 순위 확인" 버튼(POST /ads/rank-by-distance/run)이
  backend/app/routers/ads.py의 _run_local_crawl을 통해 이 스크립트를 직접
  트리거한다 — 더 이상 항상 수동 실행해야 하는 배치 도구는 아니다.
- _run_local_crawl은 shop_no가 있으면 크롤 실행 전에 그 캠페인의 store_id로
  배민 store_platform_connections 자격증명을 복호화해 로그인하고,
  backend/scrapers/baemin_stats.py의 fetch_shop_info(GET
  /v4/store/shops/{shopNo})로 사장님광장에서 상호명/카테고리/도로명주소/좌표를
  가로채 크롤러 서브프로세스에 STORE_DISPLAY_NAME/CATEGORY_LABEL/STORE_ADDRESS/
  STORE_LAT/STORE_LNG로 주입한다 — 가게마다 .env를 손으로 고칠 필요가 없다.
  이 단계가 실패하면 .env 폴백 없이 하드 에러로 크롤을 중단한다. shop_no가
  없는 캠페인은 기존처럼 crawler/.env 값을 그대로 쓴다.
- backend/scripts/ingest_rank_snapshots.py: run_crawl.py 종료 직후
  _run_local_crawl이 같은 함수 안에서 자동으로 호출한다(별도 수동 스텝
  아님). results.csv를 읽어 해당 캠페인의 ad_rank_snapshots에
  distance_km/point_label/total_scanned/ads_above와 함께 적재한다. rank가
  숫자가 아닌 행(NOT_FOUND/NAV_ERROR 등, 예: 매장이 영업시간 외라 카테고리
  리스트에 아예 안 뜨는 경우)은 적재하지 않고 스킵한다. 경쟁 가게 CPC는
  실측할 수 없으므로 이 종류의 행에는 저장하지 않는다. 크롤러가 남기는
  timestamp는 tzinfo 없는 KST 문자열이라, TIMESTAMPTZ 컬럼에 그대로 넣으면
  UTC로 오해돼 9시간 밀린다 — Asia/Seoul을 명시적으로 attach한 뒤 저장한다.
- GET /ads/rank-by-distance: 캠페인별로 point_label 최신 값을 거리순으로
  반환한다. 사이트는 DB에 이미 적재된 값을 조회만 할 뿐, 요청을 받을 때마다
  실시간으로 배민 앱을 크롤링하지 않는다.
- seed.sql은 distance_km IS NOT NULL인 실측 스냅샷 행을 심지 않는다. 이 값은
  crawler/가 실제로 측정한 결과만 들어가야 하는 자리라, seed로 가짜 값을
  넣으면 한 번도 크롤을 안 돌린 새 환경에서도 실측인 것처럼 보인다. 실측
  전에는 GET /ads/rank-monitoring이 current_rank/rank_status를 null로
  반환하는 게 정상 동작이다.
- "적용하기"(POST /ads/rank-by-distance/apply-bid, 배민에 실제 CPC 반영)도
  로컬 crawler venv가 없는 배포 환경에서는 크롤 재측정뿐 아니라 배민
  로그인+입찰 제출(submit_cpc_bid) 전체를 CRAWL_WORKER_URL의 새 엔드포인트
  POST /internal/apply-bid로 위임한다(2026-08-18 수정). 원래는 로그인만
  Railway 프로세스 자신이 직접 실행했는데, Railway의 클라우드 IP에서
  로그인 폼 자체가 렌더링 안 되고 아이디 입력창 fill()이 30초 타임아웃으로
  막히는 게 실측 확인됐다(배민 봇 탐지로 추정) — 리뷰/매출/우가클 스크래핑과
  동일하게 이 로그인도 워커(홈 IP)에서 실행해야 한다. 이 버그는 또 GET
  .../run/status가 CRAWL_WORKER_URL이 설정된 환경에서 항상 워커의 job state만
  조회하고 Railway 자신의 로컬 job state는 절대 보지 않는 구조와 겹쳐서,
  로그인 실패 메시지가 프론트에 전혀 노출되지 않고 무한 폴링("idle")으로만
  보이는 증상까지 함께 일으켰다 — 로그인 자체를 워커로 옮기면서 이 라우팅
  불일치도 함께 해소됐다(입찰 반영 경로 전체가 워커의 job state 하나로
  통일된다). crawler/.env.worker에는 CREDENTIAL_ENCRYPTION_KEY도 없었다는
  것도 같이 발견했다 — 배민 자격증명 복호화(decrypt_credential)가 리뷰/매출
  스크래핑뿐 아니라 shop_no 있는 캠페인의 크롤(_run_local_crawl)에도 이미
  필요했던 값이라, 이전에도 실제로는 누락 상태였을 수 있다.

## 포함 기능
대시보드 요약, 매출/입금 기간 토글(1일/1주/1개월/이번달), 리뷰 관리,
답글 스타일 설정, 주문내역, 재주문율, 광고 성과, 광고 순위 모니터링,
가게-플랫폼 연결, 구독 관리(결제 포함).

## 작업 순서
1. schema.sql — PostgreSQL 스키마. 16개 테이블, 외래키, ON DELETE 정책 포함.
2. seed.sql — Mock 데이터. 개인정보 없음, phone_hash와 Mock 값 사용.
3. backend — FastAPI Mock API. 최소 엔드포인트:
   GET /dashboard
   GET /reviews
   POST /reviews/{review_id}/generate-reply
   GET /orders
   GET /reply-styles
   GET /ads/performance
   GET /ads/rank-monitoring
   GET /ads/rank-by-distance
   GET /sales/summary?period=day|week|month|this_month
   GET /deposits/summary?period=day|week|month|this_month
   광고비율 계산은 별도 파일(acos.py)에 실제 공식으로 구현한다.
4. frontend — Next.js 최소 화면: 대시보드, 리뷰 관리, 광고 순위 모니터링.
   대시보드에는 매출/입금 기간 토글 버튼을 넣고, 클릭 시 API를 호출해 값이 바뀌게 한다.
   디자인: 다크 테마, 사이드바 내비게이션 (Dabang 대시보드 레이아웃 참조, 세일즈랩 화면 복제 금지).

## 추가 합의 사항 (대화에서 확정)
- 로그인: 이메일 기반 로그인(이메일 인증 위자드 포함) + 카카오 소셜 로그인
  병행 (네이버/구글/애플은 아직 범위 밖). 상세는 위 "카카오 소셜 로그인",
  "이메일 인증" 절 참고.
- 결제/구독 결제 플로우: 추후. subscriptions 테이블과 플랜 표시까지만.
- users에 password_hash 컬럼 추가 (bcrypt, 이메일 로그인용. 비밀번호 원문 저장 금지).
- orders에 platform_id FK 추가 (주문내역 플랫폼 표시, 플랫폼별 분석용).
- ad_performance_metrics는 원본(광고비·클릭수·광고주문수·광고매출)만 저장.
  CPC/CVR/AOV/ACoS/점수는 acos.py가 조회 시 실제 공식으로 계산 (정규화 원칙 준수).
- Alembic 제거. schema.sql + seed.sql이 DB 정본 산출물. SQLAlchemy 모델은 schema.sql에 1:1로 맞춤.
