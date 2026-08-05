"""JWT 세션 + bcrypt 비밀번호 해시 — 이메일 로그인과 카카오 로그인이 공유하는 인증 유틸리티."""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production-min-32-bytes")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7

_bearer = HTTPBearer(auto_error=False)


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode(), hashed.encode())


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "유효하지 않은 토큰입니다")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "사용자를 찾을 수 없습니다")
    return user


def get_user_default_store_id(user: User, db: Session) -> int:
    from app.models import Store

    store_id = db.scalar(select(Store.id).where(Store.user_id == user.id).order_by(Store.id))
    if store_id is None:
        raise HTTPException(404, "연결된 매장이 없습니다")
    return store_id
