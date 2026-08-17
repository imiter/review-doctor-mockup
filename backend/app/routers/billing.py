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
    plan: str
    expires_at: date


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
        if result.get("status") != "DONE" or result.get("totalAmount") != payment.amount:
            raise TossConfirmError(f"결제가 완료되지 않았습니다 (status={result.get('status')})")
    except TossTransportError:
        # 토스한테 물어보지도 못한 상황(타임아웃/설정 오류) — payment는 pending으로
        # 남겨둬서 재시도 가능하게 한다. 내부 설정값이 노출되지 않게 메시지는 일반화한다.
        raise HTTPException(503, "결제 확인 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
    except TossConfirmError as e:
        payment.status = "failed"
        payment.fail_reason = str(e)[:200]
        db.commit()
        raise HTTPException(402, "결제가 완료되지 않았습니다. 다시 시도해주세요.")

    payment.status = "approved"
    payment.toss_payment_key = body.payment_key
    payment.approved_at = datetime.now(timezone.utc)

    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if sub is None:
        sub = Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=kst_today())
        db.add(sub)
        db.flush()

    today = kst_today()
    base = sub.expires_at if (sub.expires_at is not None and sub.expires_at > today) else today
    sub.plan = "pro"
    sub.expires_at = add_one_month(base)

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
