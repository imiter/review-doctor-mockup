# 배민 매출·입금·재주문율 실데이터 연동 — 설계

날짜: 2026-08-11
관련 결정: CLAUDE.md "방향 전환" 로드맵 3번("실제 배달 플랫폼 데이터 연동")의
두 번째 단계. 배민 리뷰 연동(2026-08-09)에 이어 대시보드의 매출/입금/재주문율
카드를 실데이터로 교체한다. 배민만, 쿠팡이츠/요기요는 이번에도 범위 밖.

## 배경 / 목적

지금까지 대시보드의 매출분석·재주문율·매출·입금 카드는 전부 `seed.sql`의
Mock 데이터(`daily_settlements`, `repurchase_metrics`)를 집계한 값이다.
실제 배민 계정으로 사장님광장을 로그인해서 이 세 지표를 실데이터로 가져온다.

## 조사 과정에서 확인된 사실 (중요)

실제 계정으로 Playwright 진단 스크립트를 돌려 사장님광장의 "주문내역",
"정산내역", "가게통계" 화면이 부르는 내부 API를 직접 캡처해 확인했다:

- **매출**: `GET /v3/statistics/orders/summary?shopNumber={shopNo}&period=MONTH&month=YYYY-MM`
  — 매장(브랜드) 단위로 그 달의 일별 매출 그래프(`graph.data: [{x: 날짜, y: 금액}]`)와
  월 합계(`orderAmount`, `orderCount`)를 한 번의 호출로 준다. 개별 주문을
  하나씩 긁을 필요가 없다.
- **입금**: `GET /v3/settle/history/summary?settleType=ALL&startDate=&endDate=&shopOwnerNumber={사업자번호}&page=&size=`
  — **매장(브랜드) 필터 파라미터가 없다.** 정산내역 화면의 "필터" 버튼도
  열어서 확인했는데 "서비스"(전체/음식배달/배민/배민라이더스 등) 구분만
  있고 매장 선택은 없다. 즉 입금은 사업자(계정) 전체 합산으로만 나온다.
  응답은 `{contents: [{giveId, depositDueDate, giveStatus, giveStartDate,
  giveEndDate, giveAmount, ...}], totalSize}` 형태의 정산 배치 목록 —
  하루 단위가 아니라 `giveStartDate`~`giveEndDate` 기간을 묶어 `depositDueDate`에
  한 번에 입금하는 구조다.
- **재주문율**: `GET /v3/dashboard/crmInfo?shopNumber={shopNo}` — 매장 단위로
  `newReorderSummary.timeNewGraph`/`timeReorderGraph`에 최근 7일치 일별
  신규주문/재주문 건수가 이미 따로 집계돼 나온다(개별 고객 식별자는 전혀
  노출되지 않지만, 배민이 내부적으로 이미 고객을 매칭해 집계값만 주는 것으로
  보인다). **날짜 파라미터가 없는 고정 최근 7일 창**이라 과거 소급 조회는
  안 된다.
- 참고로 **주문내역 API(`GET /v4/orders`)에는 고객을 구분할 값이 전혀 없다**
  (닉네임도 회원번호도 없음). 리뷰 API의 `memberNickname`/`orderCount`와
  달리 주문 단위로는 신규/재주문을 판별할 방법이 없다 — 그래서 재주문율은
  주문내역이 아니라 위 `crmInfo`(배민이 이미 계산해주는 집계값)를 쓴다.
- 이 세 API 모두 리뷰 API와 마찬가지로 로그인 세션의 쿠키 + 매 요청마다
  달라지는 동적 서명 헤더가 있어야 응답이 온다. URL을 직접 열거나 서명 없이
  호출하면 403/차단된다 — 그래서 리뷰 스크래핑과 동일하게 Playwright로 실제
  로그인한 뒤 페이지가 스스로 발생시키는 organic 응답을
  `page.on("response", ...)`로 가로채는 방식을 그대로 쓴다.

## 스코프 결정: 브랜드별 분리 안 함

배민 계정 하나가 최대 4개 매장(브랜드)을 가질 수 있고 리뷰는 브랜드별로
구분해서 보여주기로 했지만(2026-08-10, 리뷰 관리 화면 브랜드 선택 드롭다운),
매출·입금·재주문율은 **계정(사업자) 전체 합산 하나로만** 보여주기로
결정했다:

- 입금이 애초에 브랜드별로 안 나뉘어서 나온다(위 조사 결과) — 매출/재주문율만
  브랜드별로 쪼개면 입금과 기준이 달라져 혼란스럽다.
- 대시보드의 매출/입금/재주문율 카드는 원래도 "가게 하나"의 총합을 보여주는
  용도이지, 리뷰 관리 화면처럼 브랜드별로 하나하나 훑어보는 화면이 아니다.
- 이 덕분에 `daily_settlements`/`repurchase_metrics` 스키마에 `platform_shop_no`
  같은 브랜드 구분 컬럼을 추가할 필요가 없다 — **스키마 변경이 사실상 없다.**

매출/재주문율을 매장(브랜드)별로 호출한 뒤 날짜별로 합산해서 계정 전체
숫자 하나로 만든다.

## 데이터 모델 변경

기존 `daily_settlements`, `repurchase_metrics` 테이블 구조를 그대로 쓴다.
컬럼 추가나 새 테이블 없음.

- **입금 상태(예정/확정) 구분은 하지 않는다.** 배민이 주는 `giveAmount`를
  상태(`giveStatus`)와 무관하게 그대로 `daily_settlements.deposit_amount`에
  넣는다. 대시보드는 지금처럼 "매출과 입금이 다르다"는 날짜 차이로만
  보여주고, "예정/확정" 배지 같은 건 이번 범위에 넣지 않는다(필요해지면
  나중에 `deposit_status` 컬럼을 추가하는 별도 작업으로 분리).
- 기존 Mock 시드 데이터(`daily_settlements`/`repurchase_metrics`)는 배민이
  아닌 플랫폼(요기요/쿠팡이츠) 행은 그대로 Mock으로 남는다. 배민 행만 동기화
  때 실데이터로 upsert(덮어쓰기)된다 — 유니크 제약
  `(store_id, platform_id, settle_date)` / `(store_id, platform_id, metric_date)`를
  그대로 upsert 키로 쓴다.

## API 응답 매핑

### 매출 → `daily_settlements.sales_amount`

`GET /v3/statistics/orders/summary?shopNumber={shopNo}&period=MONTH&month=YYYY-MM`의
`graph.data[].{x, y}`를 날짜별로 4개 브랜드 합산 → 그 날짜의 `sales_amount`.

### 입금 → `daily_settlements.deposit_amount`

`GET /v3/settle/history/summary`의 `contents[].{depositDueDate, giveAmount}`를
`depositDueDate`를 `settle_date`로, 같은 날짜에 배치가 여러 건이면
`giveAmount`를 합산해서 `deposit_amount`로 upsert. (`giveStartDate`~`giveEndDate`가
가리키는 정산 대상 기간은 저장하지 않는다 — 우리 스키마는 "그날 입금액"만
필요하다.)

### 재주문율 → `repurchase_metrics`

`GET /v3/dashboard/crmInfo?shopNumber={shopNo}`의
`newReorderSummary.timeNewGraph.data[].{x, y}` / `timeReorderGraph.data[].{x, y}`를
날짜별로 4개 브랜드 합산:

- `new_orders` = 그 날짜의 신규주문 합산
- `repeat_orders` = 그 날짜의 재주문 합산
- `rate_raw` = `repeat_orders / (new_orders + repeat_orders)` (0건이면 0)
- `rate_adjusted` = 최근 7일 `new_orders`/`repeat_orders`를 각각 합산한 뒤 같은 공식 (기존 스키마 주석의 "보정 후 = 이전 7일 합산" 정의 그대로)

`crmInfo`가 고정 최근 7일 창만 주므로, 이 필드로 채워지는 날짜는 항상
동기화 시점 기준 최근 7일뿐이다. 그보다 오래된 날짜의 `repurchase_metrics`
행은 다음 동기화가 그 날짜를 다시 덮어쓸 기회가 없으므로 Mock 값이 계속
남는다 — **알려진 제약으로 남긴다** (대시보드가 쓰는 "가장 최근 날짜 1건"은
동기화를 주기적으로 돌리는 한 항상 실데이터다).

## 동기화 흐름 (기존 리뷰 동기화에 통합)

새 엔드포인트를 만들지 않는다. "가게 연결" 화면의 버튼 이름을 "리뷰
동기화"에서 **"데이터 동기화"로 변경**하고, 같은 로그인 세션 안에서 아래
순서로 확장한다 (`backend/app/review_sync.py`의 `_run_sync`):

1. (기존) 4개 브랜드 각각 리뷰관리 화면에서 리뷰 수집.
2. (신규) 4개 브랜드 각각 가게통계 화면(`/shops/{shopNo}/stat`)으로 이동해
   `statistics/orders/summary`(매출), `crmInfo`(재주문율) 응답 캡처.
   `month` 파라미터를 **이번 달 포함 최근 3개월**(예: 8월에 동기화하면
   6월/7월/8월)로 반복 호출(브랜드당 3회 → 4브랜드 12회)해서 매출 이력을
   백필한다. 재주문율은 위에서 설명했듯 고정 최근 7일이라 반복 호출
   불필요(브랜드당 1회).
3. (신규) 계정 전체로 정산내역 화면(`/orders/billing`)에 1회 이동해
   `settle/history/summary` 응답 캡처. `startDate`/`endDate`는 매출과
   맞춰 **오늘로부터 최근 3개월** (`startDate = 오늘 - 90일`, `endDate = 오늘`)로
   지정.
4. 날짜별로 합산해 `daily_settlements`/`repurchase_metrics`에 upsert.
5. `review_sync_jobs`를 갱신(테이블/엔드포인트 이름은 그대로 유지 — 이미
   "리뷰 동기화"보다 넓은 의미로 커지는 것뿐이고, 이름을 바꾸면 프론트/백엔드
   양쪽의 참조를 다 바꿔야 해서 이번 범위에서는 안 한다. UI 라벨만 "데이터
   동기화"로 바뀐다).

## 에러 처리

리뷰 동기화의 "브랜드 하나 실패해도 나머지는 계속" 원칙을 그대로 확장한다:

| 상황 | 처리 |
|---|---|
| 리뷰는 성공, 매출/입금/재주문율 중 일부 실패 | job은 `success`로 기록하되 `error_message`에 실패한 부분을 덧붙인다 (예: "리뷰 42건 동기화 완료. 매출 동기화 실패: ...") |
| 매출/입금/재주문율 중 서로 다른 항목이 실패 | 서로 독립적으로 실패 격리 — 재주문율 API가 막혀도 매출/입금은 계속 저장 |
| 한 브랜드의 매출/재주문율 수집 실패 | 리뷰 동기화 때와 동일하게 그 브랜드만 건너뛰고 나머지 브랜드는 계속 |
| 세 항목 모두 완전히 실패했지만 리뷰는 성공 | job은 `success` + `error_message`에 세 항목 실패 명시 (리뷰 동기화 자체의 가치는 있으므로 전체를 `failed`로 만들지 않는다) |

새 카운터 컬럼(예: `sales_days_synced`)은 추가하지 않는다 — 지금 폴링 UI가
성공/실패 여부와 메시지 정도만 보여주는 수준이라 과한 세분화다.

## 테스트 계획

- **backend (pytest)**: 세 매핑 함수(`map_sales_by_date`, `map_deposits_by_date`,
  `map_repurchase_by_date`)를 순수 함수로 분리해 실제로 캡처한 응답 형태
  그대로 fixture로 박아 단위 테스트. 여러 브랜드 응답을 날짜별로 합산하는
  로직, `rate_raw`/`rate_adjusted` 계산, 정산 배치가 여러 건 겹치는 날짜의
  `deposit_amount` 합산 로직을 각각 검증. `_run_sync` 확장 부분은 리뷰
  스크래퍼 테스트와 같은 페이크 page/response 패턴으로 통합 테스트 —
  브랜드 일부 실패 시 나머지가 계속되는지, Mock 요기요/쿠팡이츠 행이
  그대로 남는지(배민 행만 upsert되는지) 회귀 테스트로 확인.
- **frontend**: 기존 대시보드/매출 화면은 API 응답 형태가 안 바뀌므로 코드
  변경이 거의 없다 — `tsc --noEmit`. 로컬에서 실제 계정으로 "데이터
  동기화" 실행 후 대시보드 매출/입금/재주문율 카드가 실제 값으로 바뀌는지
  직접 확인.

## CLAUDE.md 갱신

"배민 리뷰 연동 (예외 허용)" 절 바로 아래에 "배민 매출·입금·재주문율 연동
(예외 허용)" 절을 추가해서, 현재 있는 "배민의 주문/정산 실데이터 연동은
여전히 범위 밖이다"라는 문구를 이 결정으로 갱신한다. 쿠팡이츠/요기요는
계속 절대 금지 유지. "절대 금지" 목록 자체의 문구는 이미 리뷰 연동 때
배민 예외로 좁혀놨으므로 추가 수정 불필요.

## 범위 밖

- 쿠팡이츠/요기요 실데이터 연동.
- 주문내역(`/reviews`처럼 개별 주문을 화면에 나열하는 "주문내역" 페이지)
  실데이터 연동 — 이번엔 대시보드 요약 지표(매출/입금/재주문율)만. 개별
  주문 목록까지 실데이터로 바꾸려면 `/v4/orders` 페이지네이션을 새로
  설계해야 해서 별도 다음 단계로 미룬다.
- 매출/입금/재주문율의 브랜드별 분리 표시(위 "스코프 결정" 참고 — 계정
  전체 합산만).
- 입금 예정/확정 상태 구분 UI.
- 매출 이력 3개월보다 오래된 과거 소급(필요해지면 백필 범위를 늘리는 별도
  작업으로).
- 재주문율의 7일보다 오래된 과거 소급 (배민 API 자체가 지원 안 함).
