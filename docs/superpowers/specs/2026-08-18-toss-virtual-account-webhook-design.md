# 토스페이먼츠 가상계좌 웹훅 연동 설계

## 배경

`docs/superpowers/specs/2026-08-17-toss-payments-subscription-design.md`(결제/구독
기본 연동)는 "스코프 밖" 절에서 웹훅을 명시적으로 다음 단계로 미뤘다. 이번에 그
다음 단계를 진행한다 — 가상계좌 결제수단을 실제로 쓸 수 있게 만드는 것.

최종 리뷰(전체 브랜치 리뷰) 과정에서, 결제 승인 시점의 Critical 버그(토스 응답의
`status`를 안 봐서 미결제 건도 승인되던 문제)를 고치면서 새로운 문제가 드러났다:
가상계좌를 선택하면 토스 승인 API가 HTTP 200과 함께 `status: "WAITING_FOR_DEPOSIT"`
(아직 입금 안 됨, 정상 상태)를 돌려주는데, 방금 고친 로직은 `DONE`이 아닌 모든
status를 실패로 간주해버린다 — 즉 가상계좌를 선택한 손님은 입금할 기회도 없이
주문이 죽는다. 이걸 바로잡으면서 실제로 입금이 완료됐을 때 자동으로 구독을
승인하는 웹훅까지 이번에 만든다.

토스 웹훅 문서(`docs.tosspayments.com`) 확인 결과:
- 가상계좌 입금/입금취소 이벤트는 `DEPOSIT_CALLBACK` 하나뿐이다.
- 페이로드: `{createdAt, secret, status, transactionKey, orderId}`.
- 검증 방식은 서명 헤더가 아니라 **`secret` 값 대조**다 — "결제 승인 API의 응답으로
  돌아온 `secret`과 같으면 정상적인 요청"(HMAC 서명 헤더 `tosspayments-webhook-signature`는
  `payout.changed`/`seller.changed` 웹훅에만 있고 `DEPOSIT_CALLBACK`에는 없다).
- 정확히 `secret`이 confirm 응답의 어느 경로(최상위 `secret`인지 `virtualAccount.secret`인지)에
  있는지는 이 세션의 자동 조회로는 확정 못 했다 — **구현 시점에 실제 토스 테스트
  계정으로 가상계좌 confirm 응답을 한 번 실측해서 정확한 필드 경로를 확인한다**
  (이 저장소의 "실 계정 라이브 검증" 컨벤션과 동일하게 취급).

사용자가 이미 토스 개발자센터 웹훅 메뉴에서 이 프로젝트의 Railway 백엔드 URL
(`https://backend-production-a8ace.up.railway.app/billing/webhook`)을 웹훅
URL로 등록해뒀다 — 단, 이 엔드포인트도, 이번 결제 기능 전체도 아직 Railway에
배포되지 않았다(로컬 main에만 있음). 배포는 이번 스코프 밖이고, 로컬 테스트까지가
목표다.

## 1. `payments` 테이블 컬럼 추가 (23번째 변경 아님 — 기존 22번째 테이블 컬럼 추가)

```sql
-- payments 테이블에 컬럼 추가 (schema.sql의 기존 CREATE TABLE payments 블록 안에 추가)
virtual_account_secret VARCHAR(64)
```

가상계좌 발급 시 토스가 돌려주는 `secret`을 저장해뒀다가, 나중에 웹훅이 왔을 때
대조한다. nullable — 카드 결제 등 가상계좌가 아닌 주문은 계속 NULL이다.

## 2. `confirm()` 흐름 확장 — WAITING_FOR_DEPOSIT을 실패가 아닌 세 번째 상태로 분기

기존(2026-08-17 스펙 + 최종리뷰 수정 후) 흐름은 승인(`DONE`)과 실패(그 외 전부)
둘로만 나뉘었다. 가상계좌를 위해 "입금 대기중"을 세 번째 결과로 추가한다:

- 토스 응답 `status == "WAITING_FOR_DEPOSIT"`: 실패 처리하지 않는다. 응답에 담긴
  가상계좌 정보(은행명/계좌번호/입금기한)와 `secret`을 `payments`에 저장하고
  (`virtual_account_secret`), **`payments.status`는 `pending` 그대로 유지**한다
  (승인도 실패도 아직 아니므로). 구독도 건드리지 않는다. 프론트에는 이 계좌
  정보를 그대로 돌려줘서 "이 계좌로 입금해주세요" 화면을 보여준다.
- 토스 응답 `status == "DONE"` + `totalAmount == payment.amount`: 기존과 동일하게
  즉시 승인(카드 등 즉시결제 수단).
- 그 외(`DONE`인데 금액 불일치, 또는 `DONE`/`WAITING_FOR_DEPOSIT` 둘 다 아닌 값):
  기존과 동일하게 실패 처리.

`ConfirmResponse`에 선택 필드로 가상계좌 정보(은행 코드, 계좌번호, 입금기한)를
추가하고, `status` 필드가 `"approved"`뿐 아니라 `"waiting_for_deposit"`도 반환할
수 있게 한다.

## 3. `POST /billing/webhook` — 입금 완료 통지 수신

인증 없음(토스가 호출하는 서버-투-서버 엔드포인트라 로그인 세션이 없다) — 대신
`secret` 대조가 인증을 대신한다:

1. 요청 바디에서 `orderId`, `secret`, `status`를 읽는다.
2. `orderId`로 `payments` 조회. 없으면 조용히 200 반환(토스가 재시도하지 않도록 —
   모르는 주문에 에러를 주면 토스가 계속 재시도할 수 있음, 4xx/5xx는 피한다).
3. `payment.status != "pending"`이면(이미 승인/실패 처리된 주문 — 웹훅 중복 발송
   등) 그대로 200만 반환하고 아무것도 안 한다(멱등성).
4. `payment.virtual_account_secret`이 요청의 `secret`과 다르면 아무것도 안 하고
   200만 반환(로그만 남김) — 위조된 요청을 승인 처리하면 안 되지만, 토스에게는
   4xx를 주지 않는다(토스 웹훅 컨벤션 확인 필요 — 불일치를 4xx로 알려야 토스가
   재시도를 멈추는지, 아니면 항상 200이 맞는지는 구현 시점에 문서로 재확인).
5. 검증 통과 + `status`가 입금 완료를 의미하면: 기존 `confirm()`의 승인 로직(구독
   `plan='pro'`, `expires_at` 갱신, `payments.status='approved'`)을 재사용한다 —
   `confirm()` 안의 승인 처리 블록을 별도 함수로 뽑아서 웹훅 핸들러와 공유한다.
6. 검증 통과 + `status`가 입금 취소/만료를 의미하면: `payments.status='failed'`.

## 4. 프론트엔드

**결제위젯 결제수단 제한**: `renderPaymentMethods()` 호출 시 카드와 가상계좌만
보이게 설정한다(계좌이체·휴대폰 결제 등 다른 비동기 수단은 각각 별도 웹훅
이벤트가 필요한데 이번 스코프에서 안 만드므로, 그대로 열어두면 같은 "먹튀"
버그가 다른 수단에서 재발한다). 정확한 설정 방법(허용 수단 화이트리스트 파라미터)은
구현 시점에 SDK 타입 정의를 실제로 열어서 확인한다(Task 7 때와 동일한 방식).

**`/account/billing/success` 화면**: `POST /billing/confirm` 응답의 `status`가
`"waiting_for_deposit"`이면 기존 성공/실패 두 상태 외에 세 번째 화면을 보여준다 —
입금 계좌 정보(은행/계좌번호/입금기한)와 "입금이 완료되면 자동으로 Pro로
전환됩니다"라는 안내, 그리고 지금 상태를 다시 확인할 수 있는 "새로고침" 버튼
(`refreshBilling()` 재호출 — 웹훅이 도착했으면 이미 Pro로 바뀌어있을 것).

## 5. 에러 처리

- 웹훅 핸들러는 알 수 없는 `orderId`, 이미 처리된 주문, `secret` 불일치 전부
  **예외를 던지지 않고 200으로 조용히 무시**한다 — 토스 쪽에 4xx/5xx를 주면
  불필요한 재시도가 쌓일 수 있고, 이 엔드포인트가 공격자가 임의의 `orderId`를
  넣어 상태를 캐내는 오라클이 되면 안 되므로 응답 내용에 차이를 두지 않는다.
- `secret` 불일치는 로그로 남긴다(감사 목적) — 단, 로그에 `secret` 값 자체는
  남기지 않는다(그 값 자체가 검증 비밀이므로).

## 6. 테스트 계획

**pytest**:
- `confirm()`이 `status="WAITING_FOR_DEPOSIT"` 응답을 받으면 `payments.status`가
  `pending` 그대로 유지되고(실패 처리 안 됨), `virtual_account_secret`이 저장되고,
  구독이 안 바뀌는지.
- `POST /billing/webhook`: 올바른 `secret` + 입금완료 status → 구독이 Pro로
  올라가고 `payments.status='approved'`가 되는지. `secret` 불일치 → 200이지만
  구독/payments 상태 변화 없는지. 모르는 `orderId` → 200이지만 아무 사이드이펙트
  없는지. 이미 `approved`인 주문에 같은 웹훅이 두 번 오면(중복 발송) 구독이
  두 번 연장되지 않는지(멱등성 — 기존 `confirm()`의 이중승인 방지 row lock 패턴
  재사용).
- 확장된 `_approve_payment` 공유 헬퍼가 `confirm()` 경로와 웹훅 경로 양쪽에서
  동일하게 동작하는지(달력월 계산, 기존 Pro 기간 이어붙이기 등 기존 로직 재검증).

**라이브 검증 (자동화 불가, 이 프로젝트 컨벤션과 동일)**:
사용자가 토스 테스트 키를 발급받아 `.env`/`.env.local`에 넣은 뒤, 실제로 가상계좌를
선택해 결제위젯을 통과하고, 로컬 백엔드가 아니라 **Railway 배포본**에 웹훅이
도착하는지까지 확인하는 건 이번 스코프 밖이다(배포 자체가 스코프 밖이므로).
이번 스코프의 라이브 검증 목표는: 로컬에서 (a) 가상계좌 선택 시 정상적으로
"입금 대기" 화면이 뜨는지, (b) `POST /billing/webhook`을 로컬 curl로 직접
호출했을 때(진짜 토스가 아니라 흉내) 올바른 `secret`으로는 승인되고 틀린
`secret`으로는 무시되는지 — 두 가지면 충분하다.

## 스코프 밖 (YAGNI)

- Railway 배포, 프로덕션 DB에 `virtual_account_secret` 컬럼 반영 — 별도로 사용자와
  상의.
- `DEPOSIT_CALLBACK` 외 다른 웹훅 이벤트(`payout.changed`, `seller.changed` 등).
- 가상계좌 입금 기한 만료 시 자동 알림(이메일/카톡) — 그냥 만료되면 `failed`로만
  남는다.
- 웹훅 서명 헤더 검증(카드 계열 등 다른 결제수단이 언젠가 HMAC 서명이 있는
  웹훅을 쓰게 되면 그때 추가).
