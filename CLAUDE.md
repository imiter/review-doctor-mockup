# Delivery Review & Store Insight MVP

## 프로젝트 정체성
이 프로젝트는 배달매장 사장을 위한 DB 설계 중심 MVP다.
완성형 서비스가 아니라 데이터 모델링, 현장 문제 이해, 범위 통제 능력을
보여주는 교육 과정 과제물이다. 외부 API 연동 없이 Mock 데이터와 Mock API로만
동작한다. 리뷰닥터/세일즈랩을 벤치마크했지만 화면을 복제한 것이 아니라
분석 후 재설계했다.

기술 스택: PostgreSQL, FastAPI, Next.js.


## 절대 금지 (의도적으로 제외한 범위)
- 실제 배민/쿠팡이츠/요기요 등 플랫폼 API 연동 금지
- 실제 리뷰 크롤링 금지
- 실제 AI API 호출 금지 (답글 생성은 템플릿 기반 Mock)
- 실제 자동 답글 등록 금지
- 실제 CPC 자동 입찰 금지
- 실제 광고 순위 크롤링 및 스크린샷 판독 금지
- 실제 결제, 구독, 쿠팡이츠 출금 자동화 금지
- 실제 문자/카카오톡 발송 금지
- Flutter 앱 구현 금지, 복잡한 권한/다중 사업자 권한 관리 금지
위 기능은 전부 Mock으로 흉내만 낸다.
단, 광고비율(ACoS) 계산만 실제 공식으로 계산한다.

## 개인정보 원칙
- 실제 개인정보는 저장하지 않는다.
- 전화번호는 원문 대신 phone_hash로만 저장한다.
- 사업자번호, 스토어 아이디, 주문번호는 전부 Mock 값이다.
- 주민번호와 실명은 받지 않는다 (법적 근거 없음).
- users 테이블 컬럼은 최소화: id, email, nickname, phone_hash,
  marketing_agreed, created_at.

## DB 설계 (16개 테이블)
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, review_replies, reply_styles, reply_settings,
daily_settlements, repurchase_metrics, ad_campaigns,
ad_performance_metrics, ad_rank_snapshots, alerts.

### 테이블 용도
- users: 사장 계정. 전화번호는 phone_hash로 비식별화.
- stores: 매장 정보.
- platforms: 배달 플랫폼 마스터(배민, 쿠팡이츠 등). 로고/수수료율 확장 대비 테이블화.
- store_platform_connections: 매장과 플랫폼의 N:M 중간 테이블.
- subscriptions: Basic/Pro 플랜, 하루 답글 생성 한도, Pro 기능 잠금.
- orders: 주문내역(주문번호, 주문시각, 주문메뉴, 주문유형, 주문금액).
- reviews: 리뷰(별점, 내용, 고객 닉네임, 주문 횟수, 상태).
- review_replies: AI 추천 답글 Mock과 사장 최종 답글.
- reply_styles: 답글 말투 스타일 마스터(발랄 이모지 파티, 진중맨, 무난 요정, 진지한 하이개그).
- reply_settings: 가게별 답글 설정(홍보문구, 닉네임/메뉴/가게명 포함 여부, 부정 리뷰 홍보문구 포함 여부).
- daily_settlements: 일별 매출과 입금을 함께 저장(정산 지연 반영).
- repurchase_metrics: 날짜별 재주문율, 신규 주문 수, 재주문 수, 보정 전/후.
- ad_campaigns: 광고 캠페인(카테고리, 현재 CPC, 목표 순위).
- ad_performance_metrics: CPC, CVR, AOV, ACoS, 광고 성과 점수.
- ad_rank_snapshots: 시간별 순위 스냅샷(현재 순위, 경쟁 가게 예상 CPC, 상태).
- alerts: 부정 리뷰, 미답변, 순위 하락 알림.

### 핵심 관계 (모든 관계에 외래키와 삭제 정책 명시)
- users 1:N stores
- stores N:M platforms (중간 테이블 store_platform_connections)
- stores 1:N orders
- orders 1:1 reviews (reviews.order_id가 orders.id 참조, 핵심 외래키)
- reviews 1:N review_replies
- stores 1:1 reply_settings, reply_settings는 reply_styles 참조
- daily_settlements는 store와 platform 참조, 매출액과 입금액을 함께 가진다
- ad_campaigns는 store 참조
- ad_performance_metrics와 ad_rank_snapshots는 ad_campaigns 참조
- alerts는 store 참조

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
CPC, 상태, 추천 액션을 담는다. 실제 순위 수집(크롤링/스크린샷 판독)은 하지
않고, 수집됐다고 가정한 결과만 Mock으로 저장하고 보여준다.

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
   GET /sales/summary?period=day|week|month|this_month
   GET /deposits/summary?period=day|week|month|this_month
   광고비율 계산은 별도 파일(acos.py)에 실제 공식으로 구현한다.
4. frontend — Next.js 최소 화면: 대시보드, 리뷰 관리, 광고 순위 모니터링.
   대시보드에는 매출/입금 기간 토글 버튼을 넣고, 클릭 시 API를 호출해 값이 바뀌게 한다.
   디자인: 다크 테마, 사이드바 내비게이션 (Dabang 대시보드 레이아웃 참조, 세일즈랩 화면 복제 금지).

## 추가 합의 사항 (대화에서 확정)
- 로그인: 이메일 기반 간단 로그인 구현 (소셜 로그인 제외, 추후 추가).
- 결제/구독 결제 플로우: 추후. subscriptions 테이블과 플랜 표시까지만.
- users에 password_hash 컬럼 추가 (bcrypt, 이메일 로그인용. 비밀번호 원문 저장 금지).
- orders에 platform_id FK 추가 (주문내역 플랫폼 표시, 플랫폼별 분석용).
- ad_performance_metrics는 원본(광고비·클릭수·광고주문수·광고매출)만 저장.
  CPC/CVR/AOV/ACoS/점수는 acos.py가 조회 시 실제 공식으로 계산 (정규화 원칙 준수).
- Alembic 제거. schema.sql + seed.sql이 DB 정본 산출물. SQLAlchemy 모델은 schema.sql에 1:1로 맞춤.
