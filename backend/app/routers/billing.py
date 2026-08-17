import hmac
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Payment, Subscription, User
from app.plan import PRO_MONTHLY_PRICE, add_one_month, effective_plan, kst_today, replies_used_today
from app.toss_client import TossConfirmError, TossTransportError, confirm_payment

router = APIRouter(tags=["billing"])


class MeBillingResponse(BaseModel):
    plan: str
    is_pro: bool
    expires_at: date | None
    daily_reply_limit: int
    replies_used_today: int


@router.get("/billing/me", response_model=MeBillingResponse)
def billing_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    plan = effective_plan(sub)
    return MeBillingResponse(
        plan=plan,
        is_pro=plan == "pro",
        expires_at=sub.expires_at if sub else None,
        daily_reply_limit=sub.daily_reply_limit if sub else 10,
        replies_used_today=replies_used_today(user, db),
    )


class CheckoutResponse(BaseModel):
    order_id: str
    amount: int
    order_name: str


@router.post("/billing/checkout", response_model=CheckoutResponse)
def checkout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order_id = f"pro-{uuid.uuid4().hex}"
    payment = Payment(
        user_id=user.id, order_id=order_id, plan="pro", amount=PRO_MONTHLY_PRICE,
        status="pending", requested_at=datetime.now(timezone.utc),
    )
    db.add(payment)
    db.commit()
    return CheckoutResponse(order_id=order_id, amount=PRO_MONTHLY_PRICE, order_name="Pro 플랜 1개월")


class ConfirmRequest(BaseModel):
    order_id: str
    payment_key: str
    amount: int


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


class PaymentHistoryItem(BaseModel):
    order_id: str
    amount: int
    status: str
    requested_at: datetime
    approved_at: datetime | None

    class Config:
        from_attributes = True


@router.get("/billing/history", response_model=list[PaymentHistoryItem])
def billing_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payments = db.scalars(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.requested_at.desc())
    ).all()
    return list(payments)


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
    if not payment.virtual_account_secret or not hmac.compare_digest(payment.virtual_account_secret, body.secret or ""):
        return {"received": True}

    if body.status in _DEPOSIT_DONE_STATUSES:
        _approve_payment(payment, db)
        db.commit()
    elif body.status in _DEPOSIT_FAILED_STATUSES:
        payment.status = "failed"
        payment.fail_reason = f"가상계좌 입금 취소/만료 (status={body.status})"[:200]
        db.commit()

    return {"received": True}
