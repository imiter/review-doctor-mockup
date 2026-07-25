"""가게 연결. 매장×플랫폼 N:M 연결을 Mock으로 추가/해제한다.

실제 배달앱 계정 연동은 하지 않는다 — 연결하면 Mock 스토어 아이디/사업자번호가
즉석에서 만들어질 뿐이다.
"""

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import Store, StorePlatformConnection, User

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
