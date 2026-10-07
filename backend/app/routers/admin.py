"""관리자 전용 엔드포인트 — 결제 이력 조회, 배민 연결 매장 운영 현황, 유저 조회+플랜
수동 변경. require_admin_token(admin 전용, 사장님 users 테이블과 무관)으로
전부 보호된다. 설계 배경은
docs/superpowers/specs/2026-10-07-admin-panel-separation-design.md 참고."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Payment, Platform, ReplySetting, ReviewSyncJob, Store, StorePlatformConnection, Subscription, User
from app.plan import effective_plan, kst_today
from app.routers.admin_auth import require_admin_token

router = APIRouter(tags=["admin"])


@router.get("/admin/payments")
def admin_list_payments(
    status: str | None = None,
    limit: int = 50,
    _: None = Depends(require_admin_token),
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


@router.get("/admin/stores")
def admin_list_stores(_: None = Depends(require_admin_token), db: Session = Depends(get_db)):
    baemin = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if baemin is None:
        return []

    conns = db.scalars(
        select(StorePlatformConnection).where(
            StorePlatformConnection.platform_id == baemin.id,
            StorePlatformConnection.credential_ciphertext.is_not(None),
        )
    ).all()

    rows = []
    for conn in conns:
        store = db.get(Store, conn.store_id)
        owner = db.get(User, store.user_id)
        latest_job = db.scalar(
            select(ReviewSyncJob)
            .where(ReviewSyncJob.store_id == store.id)
            .order_by(ReviewSyncJob.started_at.desc())
            .limit(1)
        )
        rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == store.id))

        rows.append({
            "store_id": store.id,
            "store_name": store.name,
            "owner_email": owner.email,
            "owner_nickname": owner.nickname,
            "last_sync": None if latest_job is None else {
                "triggered_by": latest_job.triggered_by,
                "status": latest_job.status,
                "started_at": latest_job.started_at,
                "finished_at": latest_job.finished_at,
                "error_message": latest_job.error_message,
            },
            "auto_reply_enabled": rs.auto_reply_enabled if rs is not None else False,
        })
    return rows


class AutoReplyToggleRequest(BaseModel):
    enabled: bool


@router.patch("/admin/stores/{store_id}/auto-reply")
def admin_toggle_auto_reply(
    store_id: int,
    body: AutoReplyToggleRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == store_id))
    if rs is None:
        raise HTTPException(404, "답글 설정이 없습니다")
    rs.auto_reply_enabled = body.enabled
    db.commit()
    return {"store_id": store_id, "auto_reply_enabled": rs.auto_reply_enabled}


@router.get("/admin/users")
def admin_list_users(
    q: str | None = None,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    query = select(User).order_by(User.created_at.desc()).limit(50)
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.email.ilike(like), User.nickname.ilike(like)))
    users = db.scalars(query).all()

    rows = []
    for u in users:
        sub = db.scalar(select(Subscription).where(Subscription.user_id == u.id))
        store_count = db.scalar(select(func.count(Store.id)).where(Store.user_id == u.id)) or 0
        rows.append({
            "user_id": u.id,
            "email": u.email,
            "nickname": u.nickname,
            "created_at": u.created_at,
            "plan": effective_plan(sub),
            "expires_at": sub.expires_at if sub else None,
            "store_count": store_count,
        })
    return rows


class AdminPlanUpdateRequest(BaseModel):
    plan: Literal["basic", "pro"]
    days: int | None = None


@router.patch("/admin/users/{user_id}/plan")
def admin_set_plan(
    user_id: int,
    body: AdminPlanUpdateRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")

    days = body.days if body.days is not None else 30
    if body.plan == "pro" and not (1 <= days <= 365):
        raise HTTPException(422, "days는 1~365 사이여야 합니다")

    sub = db.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if sub is None:
        sub = Subscription(user_id=user_id, plan="basic", daily_reply_limit=10, started_at=kst_today())
        db.add(sub)
        db.flush()

    if body.plan == "pro":
        # billing.py의 _approve_payment와 동일한 패턴: 기존 만료일이 아직 남아있으면
        # 그 날짜부터 연장하고, 없거나 이미 지났으면 오늘부터 시작한다. 무조건
        # "오늘 + days"로 덮어쓰면 이미 유료로 몇 달 남은 사용자에게 관리자가 지원
        # 차원에서 며칠을 더 얹어주려다 오히려 구독 기간을 단축시키는 사고가 난다.
        today = kst_today()
        base = sub.expires_at if (sub.expires_at is not None and sub.expires_at > today) else today
        sub.plan = "pro"
        sub.expires_at = base + timedelta(days=days)
    else:
        sub.plan = "basic"
        sub.expires_at = None

    db.commit()
    return {"user_id": user_id, "plan": sub.plan, "expires_at": sub.expires_at}
