# 리뷰닥터 벤치마크 MVP — DB 설계 중심 프로토타입 설계서

- 날짜: 2026-07-25
- 작성 배경: 배달매장을 실제 운영하는 사장이 리뷰닥터/세일즈랩을 벤치마크하여 만드는 예비 창업/투자 검증용 프로토타입
- 목표: **데이터 모델링 역량, 현장 문제 이해, 범위 통제**를 보여주는 것. 완성형 서비스가 아님
- 원칙: 외부 API 연동 없음. 크롤링·자동 입찰·LLM 호출 없음. Mock 데이터(seed)와 Mock API로만 동작

## 1. 해결하려는 현장 문제

1. **리뷰 답글 노동**: 리뷰 답글이 매일 쌓이는 반복 노동이다.
2. **매출·입금 차액**: 주문 총액과 수수료 공제 후 실제 입금액이 다르다. 왜 다른지 분해해서 보기 어렵다.
3. **광고 순위 밀림**: 경쟁 가게가 CPC를 조금만 올려도 순위가 내려가서 앱을 계속 확인해야 한다.

## 2. 범위

### 포함 (In Scope)

- 세 도메인(리뷰/정산/광고) 모두: 스키마 + seed 데이터 + Mock API + 화면 1개씩 (총 3화면)
- 멀티 플랫폼(배민/쿠팡이츠/요기요) + 멀티 매장 스키마
- 리뷰: 스타일(친근함/장난꾸러기/정중함) 선택 → 템플릿 기반 Mock 답글 생성 → 수정 → 저장
- 정산: 기간/플랫폼 필터 + 주문총액 → 공제 → 실입금액 차액 분해 (조회 전용)
- 광고: 카테고리·현재 CPC·목표 순위·현재 순위·경쟁 예상 CPC·상태·추천 액션 표시 + [적용] 시 CPC 변경 및 이력 기록

### 제외 (Out of Scope)

- 로그인/회원가입 (owner 1명 고정 seed)
- 실제 크롤링, 실제 자동 입찰, 실제 LLM 호출, 외부 API 일체
- 알림/푸시, 정산 불일치 자동 감지, 순위 변동 시뮬레이션 엔진
- 결제/구독 등 SaaS 기능
- 화면 E2E 테스트 (UI는 개발하며 변경 예정)

### 향후 확장 경로 (이번엔 안 함, 스키마만 대비)

- 답글 생성: `reply_templates` 조회 지점만 LLM 호출로 교체하면 AI 스타일 답글로 전환 가능. 스키마 변경 불필요
- 순위 수집: `ad_rank_snapshots`에 seed 대신 크롤러가 행을 넣으면 됨

## 3. 아키텍처

```
review-docter/
├── backend/            # FastAPI + SQLAlchemy + Alembic
│   ├── app/models/     # 테이블 정의 = 핵심 산출물
│   ├── app/routers/    # Mock API (reviews, settlements, ads)
│   └── app/seed/       # Mock 데이터 생성 스크립트
├── frontend/           # Next.js (화면 3개)
├── docker-compose.yml  # PostgreSQL
└── docs/               # ERD, 설계 문서
```

- 데이터 흐름: Next.js → FastAPI REST API → PostgreSQL
- "Mock"인 것은 **데이터의 출처**(크롤링 대신 seed)와 **답글 생성**(LLM 대신 템플릿)뿐. API와 DB는 실제로 동작
- 기술 선택: 백엔드 FastAPI, 프론트 Next.js, DB는 Docker Compose로 PostgreSQL 구동

## 4. 데이터 모델 (테이블 16개)

```
[공통 기반]
owners ──< stores ──< store_platforms >── platforms
                           │
        ┌──────────────────┼──────────────────┐
     [리뷰]             [정산]              [광고]
     reviews            orders             ad_campaigns
       │                  │ │                 │
  review_replies   order_deductions │    ad_rank_snapshots
       │                  │         │    ad_recommendations
  reply_styles     settlements ─────┘    ad_bid_history
       │
  reply_templates                        + mock_clock (단일 행)
```

### 4.1 공통 기반 (4개)

| 테이블 | 핵심 컬럼 | 설명 |
|---|---|---|
| `owners` | name, phone | 사장. seed 1명, 로그인 없음 |
| `stores` | owner_id FK, name, address | 사장 1명이 매장 여러 개 |
| `platforms` | code, name, 기본 수수료율 | 배민/쿠팡이츠/요기요 마스터 |
| `store_platforms` | store_id FK, platform_id FK, platform_store_name | 매장×플랫폼 입점 단위. 리뷰·주문·광고가 전부 여기 연결 |

**설계 결정**: 리뷰/주문/광고를 `stores`가 아닌 `store_platforms`에 연결한다. 같은 매장이라도 플랫폼별로 리뷰·수수료·광고가 별개이기 때문. 멀티 플랫폼 확장성의 뼈대.

### 4.2 리뷰 도메인 (4개)

| 테이블 | 핵심 컬럼 | 설명 |
|---|---|---|
| `reviews` | store_platform_id FK, rating, content, reviewer_name, has_photo, status(답글대기/완료) | 수집된 리뷰 (Mock seed) |
| `reply_styles` | name, description | 친근함/장난꾸러기/정중함 등 말투 |
| `reply_templates` | style_id FK, rating_band(1–2/3/4–5점), template_text | 스타일×별점대별 답글 템플릿 |
| `review_replies` | review_id FK(unique), style_id FK, content, created_at | 저장된 답글. 생성 스타일 기록 |

**설계 결정**: 답글 생성은 `reply_templates` 조회로 채우는 Mock. 추후 이 조회 한 곳만 LLM 호출로 교체하면 AI 답글로 전환되며, `review_replies.style_id`가 이미 있어 스키마 변경이 필요 없다.

### 4.3 정산 도메인 (3개)

| 테이블 | 핵심 컬럼 | 설명 |
|---|---|---|
| `orders` | store_platform_id FK, settlement_id FK(nullable), ordered_at, item_amount, delivery_tip, status | 주문 1건 |
| `order_deductions` | order_id FK, type(중개수수료/결제수수료/배달비/광고비/할인지원), amount | 주문별 공제 내역을 타입별 행으로 |
| `settlements` | store_platform_id FK, period_start/end, payout_date, total_gross, total_deductions, net_payout, status(예정/입금완료) | 입금 주기별 집계. 소속 주문은 `orders.settlement_id`로 연결 |

**설계 결정**: 공제를 고정 컬럼이 아닌 `order_deductions` 타입별 행으로 저장한다. 플랫폼마다 공제 항목이 다르고 계속 바뀌므로, 컬럼 추가 없이 새 공제 유형을 수용한다. 차액 분해 화면은 이 테이블 집계로 바로 나온다.

### 4.4 광고 도메인 (4개 + mock_clock)

| 테이블 | 핵심 컬럼 | 설명 |
|---|---|---|
| `ad_campaigns` | store_platform_id FK, category, current_cpc, target_rank, status(운영중/일시정지) | 카테고리별 광고 운영 단위 |
| `ad_rank_snapshots` | campaign_id FK, snapshot_at, my_rank, competitor_est_cpc | 시점별 순위 기록. seed에 시계열로 심어 "실시간 밀림" 재현 |
| `ad_recommendations` | campaign_id FK, snapshot_id FK, action_type(CPC인상/유지/인하), suggested_cpc, status(대기/적용/무시) | 스냅샷 기반 추천. 적용/무시 이력이 남는다 |
| `ad_bid_history` | campaign_id FK, recommendation_id FK(nullable), old_cpc, new_cpc, applied_at | CPC 변경 이력. 추천 기반/수동 구분 |
| `mock_clock` | mock_now | 단일 행. Mock 시간의 현재 시각 |

**설계 결정**: 광고 대시보드의 7개 표시 항목(카테고리·현재CPC·목표순위·현재순위·경쟁예상CPC·상태·추천액션)은 `ad_campaigns` + 최신 `ad_rank_snapshots` + 대기 중 `ad_recommendations` 조인 한 번으로 나온다. `ad_recommendations`를 즉석 계산이 아닌 테이블로 두는 이유는 "추천을 적용했다/무시했다"의 이력 추적을 보여주기 위함 (사용자 확인 완료).

## 5. Mock API

| 메서드 | 엔드포인트 | 동작 |
|---|---|---|
| GET | `/api/reply-styles` | 답글 스타일 목록 (화면 드롭다운용) |
| GET | `/api/reviews` | 리뷰 목록 (매장·플랫폼·답글상태 필터) |
| POST | `/api/reviews/{id}/reply/draft` | 스타일 선택 → 템플릿 기반 답글 초안 반환 (저장 안 함) |
| POST | `/api/reviews/{id}/reply` | 수정된 답글 저장, 리뷰 상태 '완료' 전이 |
| GET | `/api/settlements` | 정산 목록 (기간·플랫폼 필터) |
| GET | `/api/settlements/{id}` | 차액 분해: 주문총액 → 공제 타입별 합계 → 실입금액 + 소속 주문 목록 |
| GET | `/api/ad-campaigns` | 광고 대시보드 행 (캠페인+최신 스냅샷+대기 추천 조인) |
| POST | `/api/ads/refresh` | Mock 시간 10분 전진 → 다음 스냅샷 공개 |
| POST | `/api/ad-recommendations/{id}/apply` | CPC 변경 + `bid_history` 기록 + 추천 '적용' |
| POST | `/api/ad-recommendations/{id}/dismiss` | 추천 '무시' |

### Mock 시간 메커니즘

`mock_clock.mock_now`가 현재 시각. `/api/ads/refresh`가 10분 전진시키고, 조회는 `snapshot_at <= mock_now`인 최신 스냅샷을 반환한다. seed에 미래 시점 스냅샷이 미리 있으므로 새로고침마다 순위 변동이 재현된다. 시뮬레이션 엔진 없음.

### 추천 생성 규칙 (Mock 지능의 전부)

refresh로 공개된 새 스냅샷에서 `my_rank > target_rank`이고 해당 캠페인에 대기 중 추천이 없으면, `suggested_cpc = competitor_est_cpc + 50원`으로 추천 행 1개 생성. 규칙은 이것 하나이며 더 추가하지 않는다.

## 6. Seed 전략

- **매장**: 사장 1명, 매장 2개. 1호점은 3개 플랫폼 입점, 2호점은 배민만 → 멀티 매장·멀티 플랫폼 구조를 데이터로 증명
- **주문/정산**: 최근 60일, 약 400건. 플랫폼별 공제 구조를 다르게 구성 (배민: 중개수수료+결제수수료+배달비 / 쿠팡이츠: 수수료율 상이)
- **리뷰**: 약 40건. 별점 분포 현실적으로(5점 다수, 1–2점 소수), 절반은 답글 대기
- **답글 템플릿**: 3 스타일 × 3 별점대 = 9개
- **광고**: 캠페인 2개, 캠페인당 10분 간격 스냅샷 30개 (약 5시간 분량). 경쟁 CPC 상승으로 순위가 3위→7위로 밀리는 구간 포함 (데모 하이라이트)
- **정합성 원칙**: seed는 스크립트로 생성하며 `settlements.net_payout = Σ주문총액 − Σ공제`가 항상 성립해야 한다

## 7. 에러 처리 & 테스트

- API: Pydantic 검증 + 404/409. 인증 없으므로 그 이상 하지 않음
- pytest 정합성 테스트 중심:
  1. 정산 합계 = 소속 주문 총액 − 공제 합
  2. 답글 저장 시 리뷰 상태 '답글대기' → '완료' 전이
  3. 추천 적용 시 `bid_history` 기록 + `current_cpc` 갱신 + 추천 상태 '적용'
- 프론트 E2E 테스트 없음

## 8. 브레인스토밍 중 내린 범위 결정 기록

- 화면 3개 + 모든 화면 인터랙션: 범위 3배 확장을 되물었으나 투자 검증용 전달력을 이유로 사용자가 확정
- 실제 LLM 연동: 제안에서 배제, 템플릿 Mock으로 확정 (추후 확장 경로만 설계에 반영)
- 순위 시뮬레이션 엔진: 과한 기능으로 배제, 시계열 스냅샷 seed로 대체
- 불일치 자동 감지/알림: 과한 기능으로 배제
- `ad_recommendations` 테이블 유지: 즉석 계산 대안을 제시했으나 이력 추적 가치로 유지 확정
