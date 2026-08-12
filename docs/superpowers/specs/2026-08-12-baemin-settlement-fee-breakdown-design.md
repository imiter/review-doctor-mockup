# 배민 정산 상세(수수료·배달비·고객할인·우가클비용) 실데이터 연동 — 설계

날짜: 2026-08-12
관련 결정: CLAUDE.md "방향 전환" 로드맵 3번의 연장선. 배민 매출·입금
실데이터 연동(2026-08-11)에서 "매출과 입금이 다르다"는 현장 문제를 D+3
시차로만 설명했는데, 정산이 실제로 무엇 때문에 깎이는지(수수료/배달비/
고객할인/우가클 광고비)는 여전히 `platforms.default_commission_rate` 기반
추정치였다. 이번엔 그 추정치를 배민 실측 정산 상세로 교체한다.

## 배경 / 목적

"매출 분석" 카드(`SalesBreakdownModal`)는 지금 플랫폼별로 매출액 →
중개수수료(추정) → 결제수수료(추정) → 추정 정산액 → 실제 입금액을 보여준다.
추정치는 `platforms.default_commission_rate`(요율) × 매출액,
`PAYMENT_FEE_RATE`(3% 고정) × 매출액으로 계산한 것뿐이라, 왜 추정과 실제
입금액이 차이 나는지는 알 수 없었다. 사장님이 배민 정산내역 화면에서 직접
확인 가능한 실제 차감 내역(수수료/배달비/고객할인/우가클 광고비)을 그대로
가져와 보여준다.

## 조사 과정에서 확인된 사실 (중요)

실제 계정으로 정산내역 화면(`/orders/billing`)의 각 정산 배치 카드를 클릭해
`GET /v3/settle/history/details/{giveId}`를 직접 캡처했다 (giveId
531969790, 2026.08.07~08.09 배치, 입금액 904,812원):

- 목록 화면(`settle/history/summary`)이 주는 `giveAmount`(입금액) 하나짜리
  숫자와 달리, 상세 응답엔 그 금액이 어떻게 만들어졌는지 전부 나온다:
  `baemin1Details`(한집배달·알뜰배달), `baeminDetails`(가게배달·바로결제),
  `etcDetails`(지원금/조정), `cpcDetails`(우리가게클릭 광고비) 네 블록으로
  나뉘고, 각 블록의 `giveAmount`/`total`을 다 더하면 최상위 `giveAmount`와
  정확히 일치한다(936,472 + 16,435 + 0 + (-48,095) = 904,812 — 직접 검산
  완료).
- 필요한 네 카테고리가 각 블록 안에 이렇게 들어있다:
  - **수수료**: `orderBrokerage.serviceFeeAmount.total`(중개이용료) +
    `extra.paymentFee.total`(결제수수료: 우대수수료/기본수수료 정률·정액).
    두 블록(`baemin1Details`+`baeminDetails`) 다 있음.
  - **배달비**: `delivery.deliverySupplyPrice.total`(가게가 실제로 부담하는
    배달비. 배달팁 관련 필드와는 다른 항목 — 배달팁은 아래 "범위 밖" 참고).
  - **고객할인**: `orderBrokerage.benefitsAmount.total`(즉시할인).
  - **우가클비용(광고비)**: 최상위 `cpcDetails.total` — `baemin1Details`/
    `baeminDetails`에 안 속하고 배치 전체에 하나. `dailyDetails: [{date,
    cpcAmount}]`로 일자별 세부 내역도 같이 오지만(예: 08-09 -40,966원,
    08-08 -951원, 08-07 -1,805원), 배치 자체가 여러 날에 걸치는 건 매출/
    입금과 동일한 구조라 이번에도 배치 → 입금일 하루 귀속 방식을 그대로
    따른다(아래 "스코프 결정" 참고). 일자별 `dailyDetails`는 이번 범위에서
    안 쓴다.
  - 그 외(라이더이용료, 소액주문수수료, 배달팁 즉시할인/배민클럽 할인 지원,
    VAT 조정(`deductionAmountTotalVat`), `etcDetails`의 지원금/조정 등)는
    20개 넘는 세부 항목으로 나뉘어 있고 대부분 0이거나 서로 상쇄되는 금액이라
    (예: 배민클럽 배달팁 할인 지원 128,548원과 할인 -128,548원이 순액 0)
    이번엔 개별 항목화하지 않고 "기타"로 뭉친다.
- 상세를 보려면 목록 화면에서 각 배치 카드 오른쪽 화살표(`>`)를 클릭해야
  한다 — 클릭 후에도 URL은 `/orders/billing` 그대로였다(인라인 확장 또는
  드로어로 추정, 페이지 이동 없음). 리뷰/매출과 동일하게 signed 요청이라
  `page.on("response")`로 가로채는 방식을 그대로 쓴다 — 배치 개수만큼
  카드를 순서대로 클릭해야 하므로, 배치가 여러 개면 그만큼 클릭 스텝이
  늘어난다.
- 목록 화면의 페이지네이션(이전 세션에 확정)은 "더보기"가 아니라 숫자
  페이지네이션이라는 걸 이미 알고 있다 — 상세 클릭도 각 페이지에서
  순서대로 처리한 뒤 다음 페이지로 넘어가야 한다. 카드를 클릭한 채로 다음
  카드를 클릭했을 때 이전 상세가 자동으로 접히는지, 아니면 명시적으로
  닫아야 하는지는 이번 조사에서 확인하지 않았다 — 구현 시점에 라이브
  계정으로 직접 확인해야 한다(이번 세션 내내 해온 "구현 전 실측" 원칙).

## 스코프 결정: 정산배치 → 입금일 하루 귀속 (매출/입금과 동일 패턴)

정산배치(giveId)가 여러 날짜(`giveStartDate`~`giveEndDate`)에 걸친 주문을
묶어서 하루(`depositDueDate`)에 입금하는 구조는 이미 `deposit_amount`가
쓰고 있는 패턴과 완전히 같다. 새 카테고리 4개도 같은 규칙을 따른다 —
배치의 수수료/배달비/고객할인/우가클비용을 전부 그 배치의 `depositDueDate`
하루에 합산한다. 같은 날짜에 배치가 여러 건이면(정산 요약 화면에서 이미
확인된 경우) 카테고리별로 그대로 합산.

이 방식의 한계: 배치가 3일치 주문을 묶으면 그 3일 각각의 실제 수수료가
아니라 입금일 하루에 전부 몰린다 — 이미 `deposit_amount` 자체가 갖고 있는
동일한 특성이라 새로운 비일관성을 만들지 않는다. 우가클비용만 배치 안에
일자별 세부가 있지만, 다른 세 카테고리와 다른 정밀도로 표시하면 화면에서
"왜 우가클만 날짜가 정확하지"라는 혼란을 주므로 일부러 같은 배치 귀속
방식으로 통일한다(위 조사 사실 참고).

## 데이터 모델 변경

`daily_settlements`에 nullable 컬럼 4개 추가 (기존 `sales_amount`/
`deposit_amount`와 나란히):

```sql
ALTER TABLE daily_settlements
  ADD COLUMN commission_amount INTEGER,
  ADD COLUMN delivery_fee_amount INTEGER,
  ADD COLUMN customer_discount_amount INTEGER,
  ADD COLUMN ad_cost_amount INTEGER;
```

- 전부 **양수로 저장**한다("차감된 금액"). 배민 원본 응답은 음수(예:
  `cpcDetails.total = -48095`)라 절댓값으로 뒤집어서 저장 — UI가 이미
  `−{won(...)}` 형태로 표시하는 기존 컨벤션(`commission_estimate` 등)과
  맞춘다.
- NULL 허용 이유: (1) 요기요/쿠팡이츠 행은 여전히 Mock이라 이 컬럼들을
  채우지 않는다, (2) 배민 행이라도 아직 정산 상세 동기화가 한 번도 안 된
  과거 날짜(백필 범위 밖)는 NULL로 남는다 — 0과 구분해야 "데이터 없음"과
  "차감액 0원"을 헷갈리지 않는다.
- "기타" 항목은 컬럼을 만들지 않는다. `/sales/breakdown` 조회 시점에
  `sales_amount − commission_amount − delivery_fee_amount −
  customer_discount_amount − ad_cost_amount − deposit_amount`로 계산한다.
  `sales_amount`(가게통계 화면)와 `deposit_amount`(정산내역 화면)는 서로
  다른 배민 화면에서 독립적으로 긁어온 값이라 완벽히 안 맞을 수 있는데,
  그 오차까지 포함해서 "기타"로 그대로 드러내는 게 신규 컬럼을 추가해
  억지로 맞추는 것보다 정직하다고 판단했다. 저장하지 않고 조회 시 계산하는
  건 기존 "정규화 원칙"(요약을 물리 테이블로 중복 저장하지 않는다)과도
  일치한다.
- 유니크 제약(`store_id, platform_id, settle_date`)은 그대로 upsert 키로
  쓴다 — 이미 `deposit_amount` upsert가 쓰고 있는 것과 동일한 행에 컬럼만
  추가로 채운다.

## API 응답 매핑

`GET /v3/settle/history/details/{giveId}` 응답에서 (baemin1Details +
baeminDetails 두 블록을 각각 아래처럼 더함):

```
commission_amount(배치)      = -(baemin1.orderBrokerage.serviceFeeAmount.total
                                  + baemin1.extra.paymentFee.total
                                  + baemin.orderBrokerage.serviceFeeAmount.total
                                  + baemin.extra.paymentFee.total)
delivery_fee_amount(배치)    = -(baemin1.delivery.deliverySupplyPrice.total
                                  + baemin.delivery.deliverySupplyPrice.total)
customer_discount_amount(배치) = -(baemin1.orderBrokerage.benefitsAmount.total
                                    + baemin.orderBrokerage.benefitsAmount.total)
ad_cost_amount(배치)         = -cpcDetails.total
```

같은 `depositDueDate`에 배치가 여러 건이면 각 카테고리를 배치별로 합산해
`daily_settlements`의 해당 날짜 행에 upsert(덮어쓰기, `deposit_amount`와
동일 방식).

## 동기화 흐름 (기존 "데이터 동기화"에 통합)

`fetch_account_settlement`(정산내역 목록 페이지네이션 + summary 응답 수집)
흐름 안에 상세 수집을 끼워 넣는다:

1. (기존) 정산내역 화면에서 날짜 범위 지정 후 페이지네이션을 돌며
   `settle/history/summary` 응답들을 모은다(현재도 최근 30일치 기준,
   배치 10건 안팎).
2. (신규) 각 페이지에서 카드를 순서대로 클릭해 `settle/history/details/
   {giveId}` 응답을 모은다 — 목록 페이지 하나당 카드 개수만큼 클릭.
   범위는 기존과 동일하게 30일치 전부(배치 10건 안팎) — 사용자 확인 완료.
3. `map_deposits_by_date`(기존)와 나란히 새 순수 함수
   `map_settlement_breakdown_by_date(detail_responses) -> dict[str, dict]`가
   위 매핑 공식으로 날짜별 4개 카테고리 딕셔너리를 만든다.
4. `upsert_daily_settlement`(기존, `deposit_amount`를 채우는 함수)를 확장해
   같은 upsert 호출에서 4개 신규 컬럼도 같이 채운다 — 새 upsert 함수를
   따로 만들지 않는다.

새 엔드포인트는 만들지 않는다. 기존 "데이터 동기화" 버튼 흐름 그대로.

## 에러 처리

기존 "일부 실패해도 나머지는 계속" 원칙을 그대로 확장한다:

| 상황 | 처리 |
|---|---|
| summary는 성공, 특정 배치의 detail 클릭/응답 실패 | 그 배치만 건너뛰고 나머지 배치는 계속 — `deposit_amount`(summary 기반)는 이미 정확하니 영향 없고, 그 배치가 걸친 날짜의 신규 컬럼 4개만 NULL로 남는다 |
| detail 수집 자체가 완전히 실패(예: 클릭 셀렉터를 못 찾음) | job은 `success` 유지, `error_message`에 "정산 상세 동기화 실패" 추가 — `deposit_amount`는 기존 summary 경로로 이미 채워지므로 영향 없음 |
| `sales_amount − 4개 카테고리 − deposit_amount`가 큰 음수/이상값 | 버그로 취급하지 않는다 — 위에서 설명한 대로 두 독립된 배민 화면 간 오차가 "기타"에 그대로 드러나는 게 의도된 동작 |

## API/프론트 변경

`/sales/breakdown` 응답의 배민 플랫폼 행:

- 신규 컬럼이 채워진 기간(NULL 아님)이면 `commission_estimate`/
  `payment_fee_estimate` 대신 `commission_amount`, `delivery_fee_amount`,
  `customer_discount_amount`, `ad_cost_amount`, `misc_amount`(계산값) 5개
  실제값을 응답에 넣고 `is_estimate: false`를 표시.
- 아직 데이터가 없으면(NULL) 기존처럼 요율 기반 추정치 + `is_estimate:
  true`로 폴백.
- 요기요/쿠팡이츠 행은 변경 없음 — 항상 추정치 + `is_estimate: true`.

`SalesBreakdownModal`: `is_estimate`로 분기해서 배민 실측 카드는 5줄
(수수료/배달비/고객할인/우가클비용/기타) + 실제입금액, 나머지는 기존 2줄
추정 카드를 그대로 보여준다.

## 테스트 계획

- **backend (pytest)**: `map_settlement_breakdown_by_date`를 순수 함수로
  분리해 이번에 실제로 캡처한 응답(giveId 531969790, baemin1Details/
  baeminDetails/cpcDetails 전체 구조)을 그대로 fixture로 박아 단위 테스트 —
  네 카테고리 계산이 검산 공식(총합 = giveAmount)과 맞는지, 같은 날짜에
  배치가 여러 건일 때 합산되는지 확인. `/sales/breakdown`은 신규
  컬럼이 채워진/안 채워진(NULL) 두 케이스 모두 응답 형태(`is_estimate`
  분기)를 테스트. `fetch_account_settlement`의 Playwright 클릭 로직 자체는
  이 저장소 컨벤션대로 유닛 테스트하지 않고 실계정 라이브 재현으로 검증한다
  (이번 스펙의 조사 과정이 이미 1차 재현).
- **frontend**: `tsc --noEmit`. 로컬에서 실제 계정으로 "데이터 동기화" 실행
  후 매출 분석 카드가 배민만 5줄 실측으로 바뀌고 요기요/쿠팡이츠는 기존
  추정 2줄로 남는지 직접 확인.

## CLAUDE.md 갱신

"배민 매출·입금·재주문율 연동 (예외 허용)" 절과 "배민 우리가게클릭(우가클)
브랜드별 실데이터 연동 (예외 허용)" 절 사이에 "배민 정산 상세(수수료/배달비/
고객할인/우가클비용) 연동 (예외 허용)" 절을 새로 추가한다.

## 범위 밖

- 우가클비용의 배치 내 일자별 세부(`cpcDetails.dailyDetails`) — 배치 →
  입금일 하루 귀속으로 통일(위 "스코프 결정" 참고).
- 배달팁 관련 필드(`settleBizTipAmount`, `deliveryTipInstantDiscountAmount`,
  `baeminClubInstantDiscountAmount`, `baeminDeliveryTipAmount`) — 대부분
  가게가 아니라 라이더/고객 간 정산이거나 순액이 0으로 상쇄돼 "기타"에
  뭉친다.
- 라이더이용료(`riderServiceFeeAmount`), 소액주문수수료(`smallOrderFee`),
  부분환불(`partialRefundAmount`), VAT 조정(`deductionAmountTotalVat`),
  `etcDetails`(지원금/조정) — 전부 "기타"에 뭉친다, 개별 카테고리화 안 함.
- 정산 배치별 상세 원본을 그대로 보여주는 화면(배치 단위 드릴다운 UI) —
  이번엔 기간 집계 화면(매출 분석 카드)만, "데이터 모델 변경"에서 결정한
  대로 배치 원본은 저장하지 않는다.
- 요기요/쿠팡이츠 실데이터 연동.
