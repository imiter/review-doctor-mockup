"""관리자 전용 엔드포인트 — 결제 이력 조회, 배민 연결 매장 운영 현황, 유저 조회+플랜
수동 변경. require_admin으로 전부 보호된다. 설계 배경은
docs/superpowers/specs/2026-09-01-admin-panel-design.md 참고."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import Payment, User

router = APIRouter(tags=["admin"])


@router.get("/admin/payments")
def admin_list_payments(
    status: str | None = None,
    limit: int = 50,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(Payment).order_by(Payment.requested_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Payment.status == status)
    payments = db.scalars(query).all()

    rows = []
    for p in payments:
        user = db.get(User, p.user_id)
        rows.append({
            "order_id": p.order_id,
            "user_email": user.email,
            "user_nickname": user.nickname,
            "plan": p.plan,
            "amount": p.amount,
            "status": p.status,
            "requested_at": p.requested_at,
            "approved_at": p.approved_at,
            "fail_reason": p.fail_reason,
        })
    return rows
