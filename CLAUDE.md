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
- 실제 자동 답글 등록 금지
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
승인받았다. 쿠팡이츠/요기요는 아직 미승인이라 "절대 금지" 그대로 유지, 배민의
주문/정산 실데이터 연동과 리뷰 답글 실제 자동 등록도 여전히 범위 밖이다. 설계
상세는 `docs/superpowers/specs/2026-08-09-baemin-review-scraping-design.md` 참고.

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

## DB 설계 (19개 테이블)
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, review_replies, reply_styles, reply_settings,
daily_settlements, repurchase_metrics, ad_campaigns,
ad_performance_metrics, ad_rank_snapshots, alerts, social_accounts,
signup_verifications, review_sync_jobs.

### 테이블 용도
- users: 사장 계정. 전화번호는 phone_hash로 비식별화.
- stores: 매장 정보.
- platforms: 배달 플랫폼 마스터(배민, 쿠팡이츠 등). 로고/수수료율 확장 대비 테이블화.
- store_platform_connections: 매장과 플랫폼의 N:M 중간 테이블.
- subscriptions: Basic/Pro 플랜, 하루 답글 생성 한도, Pro 기능 잠금.
- orders: 주문내역(주문번호, 주문시각, 주문메뉴, 주문유형, 주문금액).
- reviews: 리뷰(별점, 내용, 고객 닉네임, 주문 횟수, 상태). store_id/platform_id/
  menu_summary를 직접 가진다 — 주문과 독립적으로 적재 가능(배민 리뷰 API에는
  주문과 연결할 공통 키가 없음). order_id는 있으면 연결하는 선택적 FK.
- review_replies: AI 추천 답글 Mock과 사장 최종 답글.
- reply_styles: 답글 말투 스타일 마스터(발랄 이모지 파티, 진중맨, 무난 요정, 진지한 하이개그).
- reply_settings: 가게별 답글 설정(홍보문구, 닉네임/메뉴/가게명 포함 여부, 부정 리뷰 홍보문구 포함 여부).
- daily_settlements: 일별 매출과 입금을 함께 저장(정산 지연 반영).
- repurchase_metrics: 날짜별 재주문율, 신규 주문 수, 재주문 수, 보정 전/후.
- ad_campaigns: 광고 캠페인(카테고리, 현재 CPC, 목표 순위).
- ad_performance_metrics: CPC, CVR, AOV, ACoS, 광고 성과 점수.
- ad_rank_snapshots: 순위 스냅샷. 두 종류가 한 테이블에 공존한다 —
  (1) 시간별 Mock 스냅샷(distance_km NULL): 현재 순위, 경쟁 가게 예상 CPC, 상태.
  (2) 반경별 실측 스냅샷(distance_km NOT NULL): crawler/로 실제 배민 앱을
  스크롤하며 수집한 point_label/거리/순위/스캔개수/위 광고개수. 경쟁 가게
  CPC는 실측 불가능해 이 종류에는 저장하지 않는다(NULL).
- alerts: 부정 리뷰, 미답변, 순위 하락 알림.
- social_accounts: 소셜 로그인(카카오 등) 연결. provider 문자열 기반이라 확장 대비.
- signup_verifications: 이메일 회원가입 인증 코드(Resend 실발송). users를
  참조하지 않는다 — 인증이 끝난 뒤에만 계정이 생성되기 때문. purpose 컬럼은
  'phone' 값도 허용하지만 현재 코드 경로에서는 만들지 않는다(휴대폰 인증 단계를
  가입 위자드에서 뺐기 때문 — 위 "이메일 인증" 절 참고).
- review_sync_jobs: 배민 리뷰 동기화 작업 상태(pending/running/success/failed).
  "가게 연결" 화면의 "리뷰 동기화" 버튼 → 백그라운드 작업 → 폴링에 쓰인다.

### 핵심 관계 (모든 관계에 외래키와 삭제 정책 명시)
- users 1:N stores
- stores N:M platforms (중간 테이블 store_platform_connections)
- stores 1:N orders
- orders 1:1 reviews (reviews.order_id, 선택적 FK — 현재 실제로 채워지는 경우 없음)
- reviews N:1 stores, reviews N:1 platforms (직접 참조, 주문 조인 없이 조회)
- reviews 1:N review_replies
- stores 1:1 reply_settings, reply_settings는 reply_styles 참조
- daily_settlements는 store와 platform 참조, 매출액과 입금액을 함께 가진다
- ad_campaigns는 store 참조
- ad_performance_metrics와 ad_rank_snapshots는 ad_campaigns 참조
- alerts는 store 참조
- users 1:N social_accounts
- review_sync_jobs는 store, platform 참조

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
CPC, 상태, 추천 액션을 담는다. 시간별 스냅샷은 수집됐다고 가정한 결과를
Mock으로 저장하고 보여준다.

### 반경별 실측 순위 (crawler/, 예외적으로 실제 크롤링 허용)
가게 기준 거리(0km / 1.5~2.5km / 2.5~3.5km)에 따라 카테고리 내 순위가 얼마나
달라지는지 실측으로 보여주는 기능. 원래 "실제 광고 순위 크롤링 금지"
원칙이었으나, crawler/ 하위의 Appium 실기기 자동화를 실측 검증(스크롤 폭·
캡처 타이밍 튜닝 포함)한 뒤 이 기능에 한해 예외를 허용하기로 결정했다.

- crawler/run_crawl.py: 실기기에서 배민 앱을 조작해 GPS를 반경별로 이동시키며
  카테고리 리스트를 스크롤·캡처해 목표 가게의 순위를 찾는다. output/results.csv로
  저장한다. 사이트(FastAPI/Next.js)와는 별개 프로세스로, 요청 시점에 실행되지
  않고 사람이 필요할 때 수동 실행하는 배치 도구다.
- backend/scripts/ingest_rank_snapshots.py: results.csv를 읽어 해당 캠페인의
  ad_rank_snapshots에 distance_km/point_label/total_scanned/ads_above와 함께
  적재한다. 경쟁 가게 CPC는 실측할 수 없으므로 이 종류의 행에는 저장하지 않는다.
- GET /ads/rank-by-distance: 캠페인별로 point_label 최신 값을 거리순으로
  반환한다. 사이트는 DB에 이미 적재된 값을 조회만 할 뿐, 요청을 받을 때마다
  실시간으로 배민 앱을 크롤링하지 않는다.

## 포함 기능
대시보드 요약, 매출/입금 기간 토글(1일/1주/1개월/이번달), 리뷰 관리,
답글 스타일 설정, 주문내역, 재주문율, 광고 성과, 광고 순위 모니터링,
가게-플랫폼 연결, 구독 플랜.

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
