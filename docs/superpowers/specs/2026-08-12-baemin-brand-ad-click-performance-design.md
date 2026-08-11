# 배민 우리가게클릭(우가클) 브랜드별 실데이터 연동 — 설계

날짜: 2026-08-12
관련 결정: CLAUDE.md "방향 전환" 로드맵 3번의 세 번째 단계. 배민 리뷰 연동
(2026-08-09), 매출·입금·재주문율 연동(2026-08-11)에 이어 대시보드 "우가클
점수" 카드를 실데이터로 교체한다. 배민만, 쿠팡이츠/요기요는 이번에도 범위
밖.

## 배경 / 목적

대시보드의 "우가클 점수" 카드는 지금 `ad_campaigns`(카테고리 기반)/
`ad_performance_metrics`를 집계한 Mock 값이다. 사용자가 실제 배민
사장님광장의 "광고·서비스관리 → 우리가게클릭 → 마케팅 성과" 화면에서
브랜드별 노출수/클릭수/주문수/주문금액/광고비를 직접 확인했고, 이 원본
숫자에 이미 있는 ACoS 공식(`acos.py`)을 그대로 대입하면 브랜드별 우가클
점수를 낼 수 있다는 걸 확인해 이번 연동을 시작했다.

## 조사 과정에서 확인된 사실

실제 계정으로 Playwright 진단 스크립트를 돌려 확인했다:

- **화면 경로**: 왼쪽 사이드바 "광고·서비스관리"(가운뎃점 문자 `・`, 공백
  아님) → 브랜드별 캠페인 목록에서 "우리가게클릭" 섹션의 캠페인 행을
  클릭 → "마케팅 성과" 페이지가 열리고 상단 탭(타임세일/즉시할인/배민클럽/
  **우리가게클릭**/한그릇) 중 "우리가게클릭" 탭이 선택된 상태로 진입한다.
  URL은 `https://self.baemin.com/shops/{shopNumber}/stat/marketing/woori-shop-click`
  — **shopNumber(브랜드) 단위 화면**이라 계정에 연결된 4개 브랜드 각각
  따로 들어가야 한다(가게통계·정산내역과 달리 계정 전체 통합 화면이 아님).
- **API**: `GET /v2/statistics/campaign/cpc/metrics/{shopNumber}?startDate=YYYY-MM-01&endDate=YYYY-MM-말일`
  — 응답 형태:
  ```json
  {
    "summary": {"displayCount": 201, "clickCount": 6, "orderCount": 0, "orderAmounts": 0, "spentBudget": 570, ...},
    "metrics": { "displayCount": [...], "clickCount": [...], "orderCount": [...], "orderAmounts": [...] },
    "dailyMetrics": [
      {"date": "2026-08-01", "spentBudget": 95, "displayCount": 40, "clickCount": 1, "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
      ...
    ]
  }
  ```
  `dailyMetrics`가 날짜별로 이미 깔끔하게 정리돼 있어 별도 매핑/합산 없이
  그대로 upsert 대상으로 쓸 수 있다. `metrics`는 `dailyMetrics`와 동일한
  정보를 필드별 배열로 중복 제공하는 것으로 보여 쓰지 않는다.
- **월 선택**: 화면 상단 "N월" 라벨을 클릭하면 "기간" 다이얼로그가 열리고,
  그 안의 **네이티브 `<select>`**(다이얼로그·커스텀 리스트 아님)에
  최근 **12개월(1년, 이번 달 포함)**이 옵션으로 들어있다 — 가게통계
  화면과 달리 진행 중인 이번 달도 선택 가능하고, 완료 여부와 무관하게
  과거 어느 달이든 선택 즉시 그 달의 새 API 응답이 organic하게 발생하는
  것을 실측 확인했다(6월 선택 → `startDate=2026-06-01&endDate=2026-06-30`
  요청이 새로 나가고 6월 실데이터를 정상 반환).
- **페이지네이션 없음**: `dailyMetrics`가 한 응답에 그 달 전체 일수만큼
  이미 다 들어있다(8월 31일 요청 시 31개 항목). 주문내역 때와 같은 숨은
  페이지네이션 문제가 없다 — 응답 하나가 통째로 그 달 전체다.
- 다른 배민 API와 마찬가지로 로그인 세션 쿠키 + 동적 서명 헤더가 필요해
  Playwright organic 캡처 방식을 그대로 쓴다.

## 스코프 결정: 브랜드별로 완전히 분리, 기존 광고 순위 모니터링과는 별개

- 이번 "우가클 점수" 실데이터는 **브랜드(shop_no) 단위**로 완전히 새로
  만든다 — 계정 전체 합산이었던 매출/입금/재주문율과 다르게, 우가클은
  애초에 브랜드별로만 조회되는 화면이라 합산할 이유도 없고 사용자도
  브랜드별 점수를 원한다.
- 기존 "광고 순위 모니터링"(`ad_campaigns`/`ad_rank_snapshots`, 카테고리
  내 순위, `crawler/`의 Appium 실측)은 **완전히 별개로 그대로 둔다.**
  같은 "광고" 도메인이지만 다른 화면·다른 지표(순위 vs 클릭 성과)라
  섞지 않는다.

## 데이터 모델

새 테이블 하나를 추가한다. 기존 `ad_campaigns`(카테고리 NOT NULL, 브랜드
개념 없음)/`ad_performance_metrics`(campaign_id NOT NULL FK)를 억지로
재사용하면 브랜드마다 가짜 카테고리·가짜 캠페인 행을 만들어야 해서 오히려
더 헷갈린다 — 그래서 분리한다.

```sql
CREATE TABLE brand_ad_click_metrics (
    id BIGSERIAL PRIMARY KEY,
    store_id BIGINT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    platform_id INTEGER NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    shop_no VARCHAR(20) NOT NULL,  -- baemin_shop_brands.shop_no와 동일한 값(문자열)
    metric_date DATE NOT NULL,
    ad_spend INTEGER NOT NULL DEFAULT 0,     -- spentBudget
    impressions INTEGER NOT NULL DEFAULT 0,  -- displayCount
    clicks INTEGER NOT NULL DEFAULT 0,       -- clickCount
    ad_orders INTEGER NOT NULL DEFAULT 0,    -- orderCount
    ad_revenue INTEGER NOT NULL DEFAULT 0,   -- orderAmounts
    UNIQUE (store_id, platform_id, shop_no, metric_date)
);
```

`daily_settlements`/`ad_performance_metrics`와 같은 정규화 원칙을 따른다
— **원본 숫자만 저장**하고 CPC/CVR/AOV/ACoS/점수는 저장하지 않는다. 조회
시점에 기존 `acos.py`의 `calculate_performance`를 그대로 재사용해 계산한다
(공식 자체는 이미 실제 공식이므로 바꿀 게 없다).

Mock 데이터는 전혀 만들지 않는다 — 이 테이블은 실제 동기화로만 채워지고,
동기화 전에는 그냥 빈 테이블이다(기존 `ad_campaigns`처럼 seed.sql이
만드는 Mock 캠페인과 뒤섞이지 않는다).

## 동기화 흐름 (기존 "데이터 동기화" 작업에 통합)

새 엔드포인트/새 버튼을 만들지 않는다. 같은 로그인 세션의 `_run_sync`
안에, 지금 있는 리뷰 → 매출(가게통계) → 이번달 매출(주문내역) → 재주문율
→ 입금 순서 뒤에 우가클 단계를 추가한다.

1. `session.shops`(4개 브랜드)를 순회하며 각 브랜드의
   `/shops/{shopNo}/stat/marketing/woori-shop-click`으로 이동.
2. 최근 3개월(이번 달 포함, 매출/입금과 동일한 백필 폭 — 매달 갱신되는
   운영 관례를 맞춘다)을 월 select로 하나씩 골라 적용 → 그때마다 새로
   발생하는 `/v2/statistics/campaign/cpc/metrics/{shopNumber}` 응답의
   `dailyMetrics`를 가로챈다(브랜드당 3회 호출 → 4브랜드 12회, 가게통계
   월 선택 로직(`_select_month_dropdown`)과 유사하지만 이쪽은 네이티브
   `<select>`라 `select_option()`으로 더 단순하게 구현된다).
3. "우리가게클릭" 캠페인 자체가 없는 브랜드(그 서비스를 안 쓰는 브랜드)는
   에러가 아니라 정상적인 빈 결과로 처리하고 다음 브랜드로 넘어간다.
4. 브랜드 × 날짜별로 `brand_ad_click_metrics`에 upsert(select-then-insert
   시 반드시 `db.flush()` — 오늘 매출/입금 동기화에서 만난 autoflush=False
   중복 INSERT 버그를 처음부터 피한다).
5. `review_sync_jobs`는 그대로 재사용(테이블/엔드포인트 이름 변경 없음,
   이미 "리뷰 동기화"보다 넓은 의미로 커져 있다).

새 파일 `backend/scrapers/baemin_ads.py`(매출/입금 스크래�퍼
`baemin_stats.py`와 관심사가 달라 분리)에 다음을 추가:

- `fetch_brand_click_metrics(page, shop_no: int, months: list[str]) -> list[dict]`
  — Playwright 화면 조작 + organic 응답 캡처. 캠페인이 없는 브랜드는 빈
  리스트를 반환한다(에러 아님).
- `map_click_metrics_by_date(responses: list[dict]) -> dict[str, dict]`
  — 순수 함수. 여러 달 응답의 `dailyMetrics`를 합쳐 날짜별
  `{ad_spend, impressions, clicks, ad_orders, ad_revenue}` dict로 만든다.

`backend/app/review_sync.py`에 추가:

- `upsert_brand_ad_click_metric(db, store_id, platform_id, shop_no, metric_date, ...)`
  — `upsert_daily_settlement`과 같은 select-then-insert-with-flush 패턴.

## 대시보드 UI

- **브랜드 목록**: 새 엔드포인트를 만들지 않는다 — 리뷰 관리 화면이 이미
  쓰고 있는 `GET /store-connections/baemin/shops?store_id=`를 그대로
  재사용한다.
- **새 엔드포인트**: `GET /ads/click-performance?store_id=&shop_no=&days=`
  — 지정한 브랜드의 `brand_ad_click_metrics`를 `days`일 집계해
  `calculate_performance`로 CPC/CVR/AOV/ACoS/점수를 계산해 반환. 기존
  `/ads/performance`(카테고리 기반, Mock)는 손대지 않고 그대로 둔다 —
  광고 순위 모니터링 화면이 계속 그걸 쓴다.
- **"우가클 점수" 카드**: 브랜드 선택 드롭다운을 추가한다(리뷰 관리
  화면과 같은 UX). 기본값은 브랜드 목록의 첫 번째. 선택한 브랜드가
  바뀌면 `/ads/click-performance`를 그 `shop_no`로 다시 호출.
- **모달(상세)**: 지금 있는 CVR/ACoS/CPC 그리드 + 종합 점수 레이아웃은
  그대로 두고 데이터 소스만 `/ads/click-performance` 응답으로 교체한다.
- **"우가클 주문 비중"(order_share)은 이번 범위에서 뺀다** — 분모(전체
  주문수)가 브랜드별로 안 나뉘어 있어서, store 전체 주문수 대비 브랜드
  하나의 광고주문수를 나누면 비율 자체가 왜곡된다. 아래 "범위 밖" 참고.

## 에러 처리

리뷰/매출 동기화의 "항목별·브랜드별 독립 실패" 원칙을 그대로 확장한다:

| 상황 | 처리 |
|---|---|
| 특정 브랜드의 우가클 캠페인이 원래 없음 | 에러 아님 — 빈 결과로 정상 스킵 |
| 특정 브랜드의 우가클 조회 자체가 실패(화면 로드/월 선택 실패 등) | 그 브랜드만 건너뛰고 나머지 브랜드는 계속. `stats_errors`에 기록해 `job.error_message`에 남김 |
| 우가클 전체가 실패해도 리뷰/매출/입금/재주문율은 성공 | job은 `success` + `error_message`에 우가클 실패만 명시(전체를 `failed`로 만들지 않음) |

## 테스트 계획

- **backend (pytest)**: `map_click_metrics_by_date`를 순수 함수로 분리해
  실제로 캡처한 `dailyMetrics` 응답 형태 그대로 fixture로 박아 단위
  테스트 — 여러 달 응답을 날짜별로 합치는 로직, 겹치는 날짜가 없는지
  검증. `upsert_brand_ad_click_metric`도 기존 `upsert_daily_settlement`
  테스트와 같은 패턴(다른 브랜드/다른 플랫폼 행은 안 건드리는지)으로
  단위 테스트. `_run_sync` 확장은 기존 통합 테스트 패턴(페이크
  page/response)으로 브랜드 일부 실패 시 나머지가 계속되는지 회귀
  테스트. `fetch_brand_click_metrics`(Playwright)는 이 세션 관례대로
  유닛 테스트 대상이 아니고 라이브 재현으로 검증(오늘 진단 스크립트로
  이미 8월/6월 두 달 모두 성공 재현 완료).
- **frontend**: `tsc --noEmit`. 로컬에서 실제 계정으로 "데이터 동기화"
  실행 후 대시보드 "우가클 점수" 카드의 브랜드 드롭다운을 바꿔가며 각
  브랜드 점수가 실제 값으로 바뀌는지 직접 확인.

## CLAUDE.md 갱신

"배민 매출·입금·재주문율 연동 (예외 허용)" 절 바로 아래에 "배민
우리가게클릭(우가클) 브랜드별 실데이터 연동 (예외 허용)" 절을 추가한다.
기존 "우가클 점수... 실제 CPC 자동 입찰 금지" 관련 서술(있다면)에 이
예외를 명시하고, 광고 순위 모니터링(`ad_rank_snapshots`, 카테고리 기반)은
여전히 별개의 Mock/실측 혼합 기능임을 분명히 한다. 쿠팡이츠/요기요는
계속 절대 금지 유지.

## 범위 밖

- 쿠팡이츠/요기요 실데이터 연동.
- 기존 "광고 순위 모니터링"(`ad_campaigns`/`ad_rank_snapshots`, 카테고리
  기반)에 대한 어떤 변경도 없음 — 완전히 별개로 유지.
- "우가클 주문 비중"(order_share) — 분모가 브랜드별로 안 나뉘어 왜곡됨
  (위 "대시보드 UI" 참고).
- 브랜드별 점수를 한 화면에 동시에 여러 개 보여주는 레이아웃(카드 여러
  개 병렬) — 이번엔 드롭다운으로 하나씩 전환.
- 최근 12개월보다 오래된 과거 소급(배민 API 자체가 최근 1년만 지원).
- 실제 CPC 자동 입찰, 예산 변경 등 우가클 캠페인 설정 자체를 건드리는
  기능 — 이번은 성과 조회(읽기)만, CLAUDE.md "절대 금지"의 "실제 CPC
  자동 입찰 금지" 원칙은 그대로 유효.
