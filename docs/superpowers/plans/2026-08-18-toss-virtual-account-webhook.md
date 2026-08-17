# 토스페이먼츠 가상계좌 웹훅 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 가상계좌 결제수단이 "돈 내고도 영영 승인 안 되는" 죽은 경로가 되지 않도록, 입금 대기 상태를 올바르게 처리하고 토스의 입금완료 웹훅을 받아 자동으로 구독을 승인한다.

**Architecture:** `POST /billing/confirm`이 토스 응답의 `WAITING_FOR_DEPOSIT` 상태를 실패가 아닌 별도 상태로 분기해 가상계좌 정보와 `secret`을 저장하고, 새 `POST /billing/webhook`이 토스가 입금 완료 시 보내는 `DEPOSIT_CALLBACK`을 받아 `secret` 대조로 검증한 뒤 기존 승인 로직(신규로 추출한 `_approve_payment` 공유 헬퍼)을 그대로 재사용해 구독을 Pro로 올린다.

**Tech Stack:** FastAPI, SQLAlchemy, Next.js/React(이미 완료된 2026-08-17 결제 기능 위에 이어붙임).

## Global Constraints

- 토스 **테스트 키**로만 검증한다. 실제 돈은 안 움직인다.
- 모든 날짜/시각 판정은 `app/plan.py`의 `kst_today()`/`add_one_month()`를 재사용한다 — 새로 날짜 계산을 만들지 않는다.
- `payments` 테이블의 새 timestamp/시각 관련 값도 tz-aware(`datetime.now(timezone.utc)`)로 생성한다(이 세션에서 반복됐던 버그 클래스).
- 웹훅 핸들러는 위조/알수없는 요청에 절대 4xx/5xx를 주지 않고 항상 200으로 조용히 무시한다(토스 재시도 폭주 방지 + 상태 오라클 방지).
- 결제 승인 로직(구독 Pro 전환, 만료일 계산)은 `confirm()`과 `webhook()` 두 곳에서 절대 따로 구현하지 않는다 — 반드시 하나의 공유 헬퍼를 통해서만 수행한다.
- 이 스코프는 로컬 테스트까지다. Railway 배포, 프로덕션 DB 컬럼 반영은 범위 밖(사용자와 별도 상의).

---

### Task 1: `payments.virtual_account_secret` 컬럼 추가

**Files:**
- Modify: `schema.sql:394-405`(`payments` 테이블 정의)
- Modify: `backend/app/models.py:108-120`(`Payment` 모델)

**Interfaces:**
- Produces: `Payment.virtual_account_secret: str | None`. Task 2/3이 이 필드를 읽고 쓴다.

- [ ] **Step 1: schema.sql에 컬럼 추가**

`schema.sql`의 `payments` 테이블 정의(394-405행)를 아래로 교체:

```sql
CREATE TABLE payments (
    id                     BIGSERIAL PRIMARY KEY,
    user_id                BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id               VARCHAR(64) NOT NULL UNIQUE,
    plan                   VARCHAR(10) NOT NULL DEFAULT 'pro',
    amount                 INT         NOT NULL,
    status                 VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'failed')),
    toss_payment_key       VARCHAR(200),
    fail_reason            VARCHAR(200),
    virtual_account_secret VARCHAR(64),  -- 가상계좌 결제일 때만 채워짐. 웹훅(DEPOSIT_CALLBACK) 검증용.
    requested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at            TIMESTAMPTZ
);

CREATE INDEX idx_payments_user ON payments(user_id);
```

- [ ] **Step 2: models.py에 컬럼 추가**

`backend/app/models.py:118`(`fail_reason` 컬럼) 다음 줄에 추가:

```python
    virtual_account_secret: Mapped[str | None] = mapped_column(String(64))
```

- [ ] **Step 3: 로컬 Postgres 개발 DB에 반영**

```bash
cd /Users/kunhee/Developer/review-docter/backend
.venv/bin/python -c "
import psycopg
conn = psycopg.connect('postgresql://postgres:postgres@localhost:15432/delivery_insight')
conn.autocommit = True
cur = conn.cursor()
cur.execute('ALTER TABLE payments ADD COLUMN IF NOT EXISTS virtual_account_secret VARCHAR(64);')
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='payments' ORDER BY ordinal_position;\")
print(cur.fetchall())
conn.close()
"
```

(`backend/.env`의 `DATABASE_URL`이 다른 값이면 그 값에 맞게 접속 문자열을 바꿔서 실행. `psql` CLI는 이 개발 환경에 없으므로 반드시 `psycopg`로 실행할 것 — 이전 태스크들도 전부 이 방식을 썼다.)

- [ ] **Step 4: 회귀 확인**

```bash
cd backend && .venv/bin/pytest -q
```
Expected: 기존 전부 PASS(새 nullable 컬럼 추가만으로는 아무 것도 안 깨져야 함).

- [ ] **Step 5: 커밋**

```bash
git add schema.sql backend/app/models.py
git commit -m "feat: payments 테이블에 virtual_account_secret 컬럼 추가 (가상계좌 웹훅 검증용)"
```

---

### Task 2: `confirm()` — WAITING_FOR_DEPOSIT을 실패가 아닌 별도 상태로 분기 + 승인 로직 공유 헬퍼 추출

**Files:**
- Modify: `backend/app/routers/billing.py`(`ConfirmResponse`, `confirm()` 전체 재작성, `_approve_payment` 신규 함수)
- Modify: `backend/tests/test_billing.py`

**Interfaces:**
- Consumes: 기존 `Payment`, `Subscription`, `effective_plan`, `add_one_month`, `kst_today`, `confirm_payment`, `TossConfirmError`, `TossTransportError`(전부 이미 import돼 있음).
- Produces: `_approve_payment(payment: Payment, db: Session) -> Subscription` — Task 3의 웹훅 핸들러가 이 함수를 그대로 가져다 쓴다. `ConfirmResponse`에 `bank_code`/`account_number`/`due_date`(전부 `str | None`) 필드 추가, `status`가 `"approved"` 외에 `"waiting_for_deposit"`도 반환 가능.

가상계좌 confirm 응답에서 `secret`/은행정보가 정확히 어느 경로에 있는지(최상위인지 `virtualAccount` 하위 객체인지)는 이 계획 작성 시점에 확정 못 했다 — 아래 Step 3 코드는 토스 API 문서 기준 최선의 추정(`result["virtualAccount"]["secret"]`, `result["virtualAccount"]["bankCode"]` 등)이다. **테스트 키가 준비돼서 실제 가상계좌 confirm 응답을 한 번이라도 실측할 수 있으면, 그 시점에 정확한 경로로 고쳐라** — 안 되면 이 추정대로 진행하고 리포트에 "실측 못 함, 추정치로 구현"이라고 명시한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_billing.py` 끝에 추가:

```python
def test_confirm_waiting_for_deposit_does_not_fail_or_approve(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()

    def fake_confirm(payment_key, order_id, amount):
        return {
            "status": "WAITING_FOR_DEPOSIT",
            "virtualAccount": {
                "secret": "va-secret-abc",
                "bankCode": "20",
                "accountNumber": "1234567890",
                "dueDate": "2026-08-25T23:59:59",
            },
        }

    monkeypatch.setattr("app.routers.billing.confirm_payment", fake_confirm)

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "va-pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "waiting_for_deposit"
    assert body["account_number"] == "1234567890"

    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "pending"  # 실패도 승인도 아님 — 웹훅을 기다리는 중
    assert payment.virtual_account_secret == "va-secret-abc"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "basic"  # 아직 안 바뀜


def test_confirm_still_rejects_non_done_non_waiting_status(client, db_session, seeded_user, auth_headers, monkeypatch):
    checkout = client.post("/billing/checkout", json={}, headers=auth_headers).json()
    monkeypatch.setattr(
        "app.routers.billing.confirm_payment",
        lambda **kw: {"status": "ABORTED", "totalAmount": 19900},
    )

    res = client.post(
        "/billing/confirm",
        json={"order_id": checkout["order_id"], "payment_key": "pk", "amount": 19900},
        headers=auth_headers,
    )
    assert res.status_code == 402
    payment = db_session.query(Payment).filter_by(order_id=checkout["order_id"]).one()
    assert payment.status == "failed"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -k waiting_for_deposit -v
```
Expected: FAIL — 지금은 `WAITING_FOR_DEPOSIT`도 그냥 실패(402) 처리되고 `virtual_account_secret`이 저장 안 됨.

- [ ] **Step 3: `billing.py` 재작성**

`backend/app/routers/billing.py`의 `ConfirmResponse`부터 `confirm()` 함수 끝까지(현재 63-109행)를 아래로 통째로 교체:

```python
class ConfirmResponse(BaseModel):
    status: str
    plan: str | None = None
    expires_at: date | None = None
    bank_code: str | None = None
    account_number: str | None = None
    due_date: str | None = None


def _approve_payment(payment: Payment, db: Session) -> Subscription:
    """토스가 실제로 결제/입금을 확인해준 뒤에만 호출한다. confirm()의 즉시결제
    경로(카드 등)와 webhook()의 가상계좌 입금완료 경로가 이 함수를 공유한다 —
    구독 승인 로직을 두 곳에 따로 만들지 않기 위함."""
    payment.status = "approved"
    payment.approved_at = datetime.now(timezone.utc)

    sub = db.scalar(select(Subscription).where(Subscription.user_id == payment.user_id))
    if sub is None:
        sub = Subscription(user_id=payment.user_id, plan="basic", daily_reply_limit=10, started_at=kst_today())
        db.add(sub)
        db.flush()

    today = kst_today()
    base = sub.expires_at if (sub.expires_at is not None and sub.expires_at > today) else today
    sub.plan = "pro"
    sub.expires_at = add_one_month(base)
    return sub


@router.post("/billing/confirm", response_model=ConfirmResponse)
def confirm(body: ConfirmRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = db.scalar(select(Payment).where(Payment.order_id == body.order_id).with_for_update())
    if payment is None or payment.user_id != user.id:
        raise HTTPException(404, "결제 요청을 찾을 수 없습니다")
    if payment.status != "pending":
        raise HTTPException(400, "이미 처리된 결제입니다")
    if payment.amount != body.amount:
        raise HTTPException(400, "결제 금액이 일치하지 않습니다")

    try:
        result = confirm_payment(payment_key=body.payment_key, order_id=payment.order_id, amount=payment.amount)
    except TossTransportError:
        # 토스한테 물어보지도 못한 상황(타임아웃/설정 오류) — payment는 pending으로
        # 남겨둬서 재시도 가능하게 한다. 내부 설정값이 노출되지 않게 메시지는 일반화한다.
        raise HTTPException(503, "결제 확인 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
    except TossConfirmError as e:
        payment.status = "failed"
        payment.fail_reason = str(e)[:200]
        db.commit()
        raise HTTPException(402, "결제가 완료되지 않았습니다. 다시 시도해주세요.")

    status = result.get("status")

    if status == "WAITING_FOR_DEPOSIT":
        # 가상계좌 — 아직 입금 전. 실패가 아니라 대기 상태다. 웹훅(POST /billing/webhook)이
        # 나중에 입금 완료를 알려주면 그때 _approve_payment를 호출한다.
        va = result.get("virtualAccount") or {}
        payment.virtual_account_secret = va.get("secret")
        payment.toss_payment_key = body.payment_key
        db.commit()
        return ConfirmResponse(
            status="waiting_for_deposit",
            bank_code=va.get("bankCode"),
            account_number=va.get("accountNumber"),
            due_date=va.get("dueDate"),
        )

    if status != "DONE" or result.get("totalAmount") != payment.amount:
        payment.status = "failed"
        payment.fail_reason = f"status={status}"[:200]
        db.commit()
        raise HTTPException(402, "결제가 완료되지 않았습니다. 다시 시도해주세요.")

    payment.toss_payment_key = body.payment_key
    sub = _approve_payment(payment, db)
    db.commit()
    return ConfirmResponse(status="approved", plan=sub.plan, expires_at=sub.expires_at)
```

(`ConfirmRequest`, import문들은 그대로 둔다 — 바뀌는 건 `ConfirmResponse`와 `confirm()`, 그리고 그 사이에 `_approve_payment`가 새로 생기는 것뿐이다.)

- [ ] **Step 4: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -v
```
Expected: 기존 테스트 전부 + 신규 2개 PASS(기존 `test_confirm_success_upgrades_subscription` 등은 여전히 `status="DONE"`을 mock하므로 그대로 통과해야 함).

- [ ] **Step 5: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/routers/billing.py backend/tests/test_billing.py
git commit -m "feat: confirm()이 가상계좌 WAITING_FOR_DEPOSIT을 실패 대신 대기 상태로 분기, 승인 로직을 _approve_payment로 추출"
```

---

### Task 3: `POST /billing/webhook` — 가상계좌 입금완료 통지 수신

**Files:**
- Modify: `backend/app/routers/billing.py`(웹훅 라우트 추가)
- Modify: `backend/tests/test_billing.py`

**Interfaces:**
- Consumes: Task 2의 `_approve_payment(payment, db)`.
- Produces: `POST /billing/webhook`(인증 없음, `secret` 대조로 검증). `main.py` 수정 불필요(같은 `billing.router`에 라우트만 추가하면 이미 등록돼 있는 라우터라 자동 반영됨).

가상계좌 입금 완료/취소 시 토스가 실제로 어떤 `status` 문자열을 보내는지도 확정 못 했다(문서에 "결제 상태"라고만 나오고 enum 값이 안 나와 있었다) — 아래 코드는 결제 승인 API와 동일하게 `"DONE"`을 완료로, 취소/만료류 값들을 실패로 취급한다. **테스트 키로 실제 가상계좌 결제를 만들어 실측할 수 있으면 정확한 값으로 맞춰라.**

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_billing.py` 끝에 추가:

```python
def _make_waiting_payment(db_session, seeded_user, order_id="va-order-1", secret="va-secret-abc"):
    payment = Payment(
        user_id=seeded_user["user"].id, order_id=order_id, plan="pro", amount=19900,
        status="pending", virtual_account_secret=secret,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(payment)
    db_session.commit()
    return payment


def test_webhook_approves_subscription_on_correct_secret_and_done_status(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user)

    res = client.post("/billing/webhook", json={"orderId": "va-order-1", "secret": "va-secret-abc", "status": "DONE"})
    assert res.status_code == 200

    payment = db_session.query(Payment).filter_by(order_id="va-order-1").one()
    assert payment.status == "approved"

    sub = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub.plan == "pro"


def test_webhook_ignores_wrong_secret(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-2")

    res = client.post("/billing/webhook", json={"orderId": "va-order-2", "secret": "wrong-secret", "status": "DONE"})
    assert res.status_code == 200  # 조용히 무시 — 4xx 안 줌

    payment = db_session.query(Payment).filter_by(order_id="va-order-2").one()
    assert payment.status == "pending"  # 안 바뀜


def test_webhook_ignores_unknown_order_id(client):
    res = client.post("/billing/webhook", json={"orderId": "no-such-order", "secret": "x", "status": "DONE"})
    assert res.status_code == 200


def test_webhook_is_idempotent_for_already_approved_payment(client, db_session, seeded_user):
    payment = _make_waiting_payment(db_session, seeded_user, order_id="va-order-3")
    from app.routers.billing import _approve_payment
    _approve_payment(payment, db_session)
    db_session.commit()
    sub_before = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    expires_before = sub_before.expires_at

    # 같은 웹훅이 중복 발송돼도 구독이 또 연장되면 안 됨
    res = client.post("/billing/webhook", json={"orderId": "va-order-3", "secret": "va-secret-abc", "status": "DONE"})
    assert res.status_code == 200

    sub_after = db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).one()
    assert sub_after.expires_at == expires_before


def test_webhook_marks_failed_on_cancel_status(client, db_session, seeded_user):
    _make_waiting_payment(db_session, seeded_user, order_id="va-order-4")

    res = client.post("/billing/webhook", json={"orderId": "va-order-4", "secret": "va-secret-abc", "status": "CANCELED"})
    assert res.status_code == 200

    payment = db_session.query(Payment).filter_by(order_id="va-order-4").one()
    assert payment.status == "failed"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -k webhook -v
```
Expected: FAIL — `POST /billing/webhook`이 아직 없어서 404.

- [ ] **Step 3: `billing.py`에 웹훅 라우트 추가**

`backend/app/routers/billing.py` 파일 끝(`billing_history` 함수 다음)에 추가:

```python
_DEPOSIT_DONE_STATUSES = {"DONE"}
_DEPOSIT_FAILED_STATUSES = {"CANCELED", "EXPIRED", "PARTIAL_CANCELED", "ABORTED"}


class WebhookPayload(BaseModel):
    orderId: str
    secret: str | None = None
    status: str


@router.post("/billing/webhook")
def billing_webhook(body: WebhookPayload, db: Session = Depends(get_db)):
    """토스가 가상계좌 입금 완료/취소 시 서버-투-서버로 호출한다(로그인 세션 없음).
    알 수 없는 요청은 절대 4xx/5xx를 주지 않고 항상 200으로 조용히 무시한다 —
    위조된 orderId로 상태를 캐내는 오라클이 되지 않도록, 그리고 토스가 불필요하게
    재시도하지 않도록."""
    payment = db.scalar(select(Payment).where(Payment.order_id == body.orderId).with_for_update())
    if payment is None:
        return {"received": True}
    if payment.status != "pending":
        return {"received": True}
    if not payment.virtual_account_secret or payment.virtual_account_secret != body.secret:
        return {"received": True}

    if body.status in _DEPOSIT_DONE_STATUSES:
        _approve_payment(payment, db)
        db.commit()
    elif body.status in _DEPOSIT_FAILED_STATUSES:
        payment.status = "failed"
        payment.fail_reason = f"가상계좌 입금 취소/만료 (status={body.status})"[:200]
        db.commit()

    return {"received": True}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

```bash
cd backend && .venv/bin/pytest tests/test_billing.py -v
```
Expected: 전부 PASS(5개 신규 웹훅 테스트 포함).

- [ ] **Step 5: 회귀 확인 + 커밋**

```bash
cd backend && .venv/bin/pytest -q
git add backend/app/routers/billing.py backend/tests/test_billing.py
git commit -m "feat: POST /billing/webhook 신설 — 가상계좌 입금완료 통지 수신 및 secret 검증"
```

---

### Task 4: 프론트엔드 — 결제 승인 화면에 "입금 대기중" 상태 추가

**Files:**
- Modify: `frontend/src/app/(app)/account/billing/success/page.tsx`(전체)

**Interfaces:**
- Consumes: Task 2의 `ConfirmResponse`(`status: "approved" | "waiting_for_deposit"`, `bank_code`/`account_number`/`due_date`), 기존 `useStoreContext().refreshBilling`/`billing`.

지금 이 페이지는 `POST /billing/confirm`의 응답 바디를 아예 안 읽고(`.then(async () => {...})`) 그냥 성공/실패만 본다 — 이제 `status` 필드를 읽어서 세 번째 화면("입금 대기")을 추가해야 한다.

- [ ] **Step 1: `success/page.tsx` 전체 교체**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ConfirmResult = {
  status: string;
  bank_code?: string | null;
  account_number?: string | null;
  due_date?: string | null;
};

export default function BillingSuccessPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refreshBilling, billing } = useStoreContext();
  const [state, setState] = useState<"loading" | "done" | "error" | "waiting">("loading");
  const [message, setMessage] = useState("");
  const [bankInfo, setBankInfo] = useState<{ bankCode: string; accountNumber: string; dueDate: string } | null>(null);

  useEffect(() => {
    const orderId = params.get("orderId");
    const paymentKey = params.get("paymentKey");
    const amount = params.get("amount");
    if (!orderId || !paymentKey || !amount) {
      setState("error");
      setMessage("결제 정보가 올바르지 않습니다.");
      return;
    }

    let cancelled = false;

    apiPost<ConfirmResult>("/billing/confirm", { order_id: orderId, payment_key: paymentKey, amount: Number(amount) })
      .then(async (result) => {
        if (cancelled) return;
        if (result.status === "waiting_for_deposit") {
          setBankInfo({
            bankCode: result.bank_code ?? "",
            accountNumber: result.account_number ?? "",
            dueDate: result.due_date ?? "",
          });
          setState("waiting");
          return;
        }
        await refreshBilling();
        if (cancelled) return;
        setState("done");
      })
      .catch((e) => {
        if (cancelled) return;
        setState("error");
        setMessage(e instanceof ApiError ? e.message : "결제 승인 중 오류가 발생했습니다.");
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 대기 화면에서 "새로고침"으로 refreshBilling()을 다시 부르면 billing 컨텍스트가
  // 갱신된다 — 그 사이 웹훅이 도착해서 is_pro가 true가 됐으면 완료 화면으로 넘어간다.
  useEffect(() => {
    if (state === "waiting" && billing?.is_pro) {
      setState("done");
    }
  }, [state, billing]);

  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      {state === "loading" && <p className="text-sm text-muted">결제를 확인하고 있어요...</p>}
      {state === "done" && (
        <>
          <p className="text-lg font-semibold">Pro 플랜이 시작됐어요!</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
      {state === "waiting" && bankInfo && (
        <>
          <p className="text-lg font-semibold">가상계좌가 발급됐어요</p>
          <div className="rounded-lg border border-border-subtle p-4 text-left text-sm">
            <p className="text-muted">은행 코드: {bankInfo.bankCode}</p>
            <p className="text-muted">계좌번호: {bankInfo.accountNumber}</p>
            {bankInfo.dueDate && <p className="text-muted">입금기한: {bankInfo.dueDate}</p>}
          </div>
          <p className="text-sm text-muted">입금이 완료되면 자동으로 Pro로 전환돼요.</p>
          <button
            onClick={() => refreshBilling()}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            새로고침
          </button>
        </>
      )}
      {state === "error" && (
        <>
          <p className="text-sm text-danger">{message}</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg border border-border-subtle px-4 py-2 text-sm"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입/빌드 확인**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: 에러 없음, `/account/billing/success`가 정적 라우트로 정상 생성.

- [ ] **Step 3: 결제수단(카드/가상계좌만 노출) 제한 여부 조사**

`frontend/src/app/(app)/account/billing/page.tsx:70`의 `renderPaymentMethods({ selector: "#toss-payment-method", variantKey: "DEFAULT" })`가 지금 토스 계정에 활성화된 모든 결제수단을 다 보여준다. 계좌이체·휴대폰 결제처럼 이번에 웹훅을 안 만든 다른 비동기 수단도 노출되면, 그 수단들에서 이번에 가상계좌에서 고친 것과 같은 "먹튀" 버그가 재발한다.

`node_modules/@tosspayments/tosspayments-sdk/types/index.d.ts`(Task 7이 이미 이 파일을 실제로 읽고 검증한 선례가 있다)를 열어서 `renderPaymentMethods`가 결제수단을 화이트리스트로 제한하는 클라이언트 사이드 파라미터를 지원하는지 확인해라.

- **지원한다면**: 카드+가상계좌만 남기도록 코드를 수정하고, 이 스텝에서 수정한 내용을 커밋에 포함해라.
- **지원 안 하고 토스 대시보드에서 "결제창 변형(variant)"을 미리 설정해야만 가능한 구조라면**(예: `variantKey`가 대시보드에서 만든 이름을 참조하는 방식): 코드는 그대로 두고, 최종 리포트에 "결제수단 제한은 코드가 아니라 토스 대시보드 설정이 필요하다 — 사용자 액션 필요"라고 명확히 남겨라. 이건 이 태스크의 실패가 아니라 정상적인 조사 결과다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/app/'(app)'/account/billing/success/page.tsx
# Step 3에서 결제수단 제한 코드를 수정했다면 frontend/src/app/'(app)'/account/billing/page.tsx도 추가
git commit -m "feat: 결제 승인 화면에 가상계좌 입금 대기 상태 추가"
```

---

### Task 5: CLAUDE.md 갱신 + 라이브 테스트 안내

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 없음(문서 전용).

- [ ] **Step 1: "결제/구독 연동" 절 갱신**

`CLAUDE.md`의 "결제/구독 연동 (예외 허용, 테스트 모드)" 절(2026-08-17에 추가된 절) 끝에 문단 추가:

```markdown

가상계좌 결제수단은 즉시 승인되지 않고 입금을 기다려야 한다 — 토스 결제승인
API가 `WAITING_FOR_DEPOSIT` 상태를 돌려주면 `POST /billing/confirm`은 실패
처리하지 않고 가상계좌 정보와 `secret`을 저장한 채 대기 상태로 남긴다. 실제
입금이 완료되면 토스가 `POST /billing/webhook`(`DEPOSIT_CALLBACK` 이벤트)을
호출하고, 저장해둔 `secret`과 대조해서 맞으면 그때 구독을 Pro로 승인한다.
승인 로직(`_approve_payment`, `backend/app/routers/billing.py`)은 즉시결제
경로와 가상계좌 경로가 공유한다. 설계 상세는
`docs/superpowers/specs/2026-08-18-toss-virtual-account-webhook-design.md`
참고.
```

- [ ] **Step 2: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: 토스페이먼츠 가상계좌 웹훅 연동 CLAUDE.md 반영"
```

---

## 이 계획 밖 (사용자가 별도로 진행할 것)

- 토스 테스트 키를 `backend/.env`(`TOSS_SECRET_KEY`)/`frontend/.env.local`(`NEXT_PUBLIC_TOSS_CLIENT_KEY`)에 채우기.
- 로컬에서 실제로 가상계좌를 선택해 결제위젯을 통과시켜, "입금 대기" 화면이 뜨는지 확인.
- `POST /billing/webhook`을 로컬 curl로 직접 호출해(진짜 토스 흉내) `secret`이 맞을 때/틀릴 때 동작 확인.
- Railway 배포, 프로덕션 DB 컬럼 반영, 실제 토스 대시보드 웹훅 등록(이미 사용자가 완료함 — Railway URL 대상, 아직 배포 전이라 실제 수신은 안 됨).
