"""가게 연결. 매장×플랫폼 N:M 연결을 관리한다.

배민은 실제 사장님광장 계정으로 로그인해 매장을 연결하고 리뷰를 동기화할 수
있다. 그 외 플랫폼(쿠팡이츠, 요기요)은 여전히 Mock — 연결하면 Mock 스토어
아이디/사업자번호가 즉석에서 만들어질 뿐, 실제 계정 연동은 하지 않는다.
"""

import random
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.credential_crypto import encrypt_credential
from app.db import get_db
from app.models import Platform, ReviewSyncJob, Store, StorePlatformConnection, User
from app.review_sync import run_review_sync_job
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login

router = APIRouter(tags=["store-connections"])


def _row(c: StorePlatformConnection) -> dict:
    return {
        "id": c.id,
        "platform_id": c.platform_id,
        "platform_code": c.platform.code,
        "platform_name": c.platform.name,
        "brand_color": c.platform.brand_color,
        "platform_store_id": c.platform_store_id,
        "business_number": c.business_number,
        "has_real_credential": c.credential_ciphertext is not None,
        "connected_at": c.connected_at.isoformat(),
    }


@router.get("/store-connections")
def list_connections(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    connections = db.scalars(
        select(StorePlatformConnection)
        .where(StorePlatformConnection.store_id == sid)
        .options(joinedload(StorePlatformConnection.platform))
        .order_by(StorePlatformConnection.connected_at)
    ).all()
    return [_row(c) for c in connections]


class ConnectRequest(BaseModel):
    platform_id: int
    store_id: int | None = None


@router.post("/store-connections", status_code=201)
def connect_platform(body: ConnectRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sid = body.store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    existing = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == body.platform_id
        )
    )
    if existing:
        raise HTTPException(409, "이미 연결된 플랫폼입니다")

    conn = StorePlatformConnection(
        store_id=sid, platform_id=body.platform_id,
        platform_store_id=f"MK-{random.randint(10_000_000, 99_999_999)}",  # Mock 스토어 아이디
        business_number=f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10000,99999)}",  # Mock
        connected_at=datetime.now(timezone.utc),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _row(conn)


@router.delete("/store-connections/{connection_id}", status_code=204)
def disconnect_platform(connection_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.get(StorePlatformConnection, connection_id, options=[joinedload(StorePlatformConnection.store)])
    if conn is None or conn.store.user_id != user.id:
        raise HTTPException(404, "연결 없음")
    db.delete(conn)
    db.commit()


class BaeminLoginRequest(BaseModel):
    platform_login_id: str
    platform_login_password: str


@router.post("/store-connections/baemin/login")
def baemin_login_endpoint(
    body: BaeminLoginRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if platform is None:
        raise HTTPException(500, "배민 플랫폼이 등록되어 있지 않습니다")

    try:
        session = baemin_login(body.platform_login_id, body.platform_login_password)
    except BaeminLoginError as e:
        raise HTTPException(401, str(e))
    shop_no, shop_name = session.shop_no, session.shop_name
    session.close()

    ciphertext = encrypt_credential(body.platform_login_id, body.platform_login_password)

    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None:
        conn = StorePlatformConnection(
            store_id=sid, platform_id=platform.id,
            platform_store_id=str(shop_no),
            connected_at=datetime.now(timezone.utc),
        )
        db.add(conn)
    else:
        conn.platform_store_id = str(shop_no)
    conn.credential_ciphertext = ciphertext
    db.commit()
    db.refresh(conn)
    return {"connected": True, "shop_name": shop_name, "platform_store_id": conn.platform_store_id}


@router.post("/store-connections/baemin/sync-reviews", status_code=202)
def start_review_sync(
    background_tasks: BackgroundTasks, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None or conn.credential_ciphertext is None:
        raise HTTPException(400, "먼저 배민 로그인이 필요합니다")

    job = ReviewSyncJob(
        store_id=sid, platform_id=platform.id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_review_sync_job, job.id)
    return {"job_id": job.id}


@router.get("/store-connections/baemin/sync-status/{job_id}")
def get_sync_status(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(ReviewSyncJob, job_id, options=[joinedload(ReviewSyncJob.store)])
    if job is None or job.store.user_id != user.id:
        raise HTTPException(404, "작업 없음")
    return {
        "id": job.id, "status": job.status,
        "reviews_fetched": job.reviews_fetched, "reviews_inserted": job.reviews_inserted,
        "error_message": job.error_message,
    }
