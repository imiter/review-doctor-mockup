"""이메일 로그인/회원가입. 소셜 로그인 없음. 전화번호는 phone_hash로만 저장."""

import hashlib
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.models import Platform, Store, StorePlatformConnection, Subscription, User

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str
    phone: str | None = None  # 원문은 저장하지 않고 즉시 해시
    marketing_agreed: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "has_phone": user.phone_hash is not None,
        "marketing_agreed": user.marketing_agreed,
    }


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    phone_hash = hashlib.sha256(body.phone.encode()).hexdigest() if body.phone else None
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        nickname=body.nickname,
        phone_hash=phone_hash,
        marketing_agreed=body.marketing_agreed,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()

    # 가입 직후 빈 대시보드를 보여주지 않도록 기본 매장 1개 + 배민 연결 + Basic 구독을 만든다.
    store = Store(
        user_id=user.id, name="내 매장", category="기타", created_at=datetime.now(timezone.utc)
    )
    db.add(store)
    db.flush()

    baemin = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if baemin:
        db.add(StorePlatformConnection(
            store_id=store.id, platform_id=baemin.id,
            platform_store_id=f"MK-{store.id:08d}", business_number="000-00-00000",
            connected_at=datetime.now(timezone.utc),
        ))
    db.add(Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))
    db.commit()

    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


class UpdateProfileRequest(BaseModel):
    nickname: str | None = None
    phone: str | None = None  # 원문은 저장하지 않고 즉시 해시
    marketing_agreed: bool | None = None


@router.patch("/me")
def update_me(body: UpdateProfileRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.nickname is not None:
        user.nickname = body.nickname
    if body.phone is not None:
        user.phone_hash = hashlib.sha256(body.phone.encode()).hexdigest()
    if body.marketing_agreed is not None:
        user.marketing_agreed = body.marketing_agreed
    db.commit()
    return _user_dict(user)
