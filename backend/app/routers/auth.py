"""이메일 로그인/회원가입(다단계 인증) + 카카오 소셜 로그인. 전화번호는 phone_hash로만 저장."""

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.email_verification import (
    EMAIL_CODE_TTL,
    MAX_ATTEMPTS,
    PHONE_CODE_TTL,
    RESEND_COOLDOWN,
    EmailSendError,
    generate_code,
    hash_code,
    send_verification_email,
)
from app.kakao import KakaoAuthError, exchange_code_for_token, fetch_kakao_user
from app.models import SignupVerification, SocialAccount, Store, Subscription, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class EmailCodeRequest(BaseModel):
    email: EmailStr


class VerifyEmailCodeRequest(BaseModel):
    email: EmailStr
    code: str


class PhoneCodeRequest(BaseModel):
    phone: str


class VerifyPhoneCodeRequest(BaseModel):
    phone: str
    code: str


class SignupRequest(BaseModel):
    email: EmailStr
    email_code: str
    phone: str
    phone_code: str
    password: str
    nickname: str
    marketing_agreed: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class KakaoCallbackRequest(BaseModel):
    code: str
    redirect_uri: str


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


def _create_default_store_and_subscription(user: User, db: Session) -> None:
    """가입 직후 기본 매장 1개 + Basic 구독을 만든다. 플랫폼 연결은 "가게 연결" 화면에서
    사용자가 직접 해야 한다 — 데모에서 그 과정을 보여줘야 하므로 자동 연결하지 않는다."""
    store = Store(
        user_id=user.id, name="내 매장", category="기타", created_at=datetime.now(timezone.utc)
    )
    db.add(store)
    db.flush()

    db.add(Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()


def _as_aware(dt: datetime) -> datetime:
    """SQLite(테스트)는 datetime을 tz 정보 없이 되돌려줄 수 있어서, naive면 UTC로
    간주한다. Postgres(TIMESTAMPTZ, 운영)는 항상 tz-aware라 이 변환이 필요 없다."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get_verification(db: Session, target: str, purpose: str) -> SignupVerification | None:
    return db.scalar(
        select(SignupVerification).where(
            SignupVerification.target == target, SignupVerification.purpose == purpose
        )
    )


def _issue_code(db: Session, target: str, purpose: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    existing = _get_verification(db, target, purpose)
    if existing and _as_aware(existing.created_at) > now - RESEND_COOLDOWN:
        raise HTTPException(429, "잠시 후 다시 시도해주세요")

    code = generate_code()
    if existing:
        existing.code_hash = hash_code(code)
        existing.expires_at = now + ttl
        existing.attempts = 0
        existing.created_at = now
    else:
        db.add(SignupVerification(
            target=target, purpose=purpose, code_hash=hash_code(code),
            expires_at=now + ttl, attempts=0, created_at=now,
        ))
    db.commit()
    return code


def _check_code(db: Session, target: str, purpose: str, code: str) -> None:
    row = _get_verification(db, target, purpose)
    if row is None or _as_aware(row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(400, "인증번호가 만료되었습니다. 다시 받아주세요")
    if row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(400, "시도 횟수를 초과했습니다. 인증번호를 다시 받아주세요")
    if row.code_hash != hash_code(code):
        row.attempts += 1
        db.commit()
        raise HTTPException(400, "인증번호가 올바르지 않습니다")


@router.post("/signup/email-code")
def request_email_code(body: EmailCodeRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    code = _issue_code(db, body.email, "email", EMAIL_CODE_TTL)
    try:
        send_verification_email(body.email, code)
    except EmailSendError as e:
        logger.warning("이메일 발송 실패 (%s): %s", body.email, e)
        raise HTTPException(502, "이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요")
    return {"sent": True}


@router.post("/signup/verify-email-code")
def verify_email_code(body: VerifyEmailCodeRequest, db: Session = Depends(get_db)):
    _check_code(db, body.email, "email", body.code)
    return {"verified": True}


@router.post("/signup/phone-code")
def request_phone_code(body: PhoneCodeRequest, db: Session = Depends(get_db)):
    code = _issue_code(db, _hash_phone(body.phone), "phone", PHONE_CODE_TTL)
    return {"mock_code": code}


@router.post("/signup/verify-phone-code")
def verify_phone_code(body: VerifyPhoneCodeRequest, db: Session = Depends(get_db)):
    _check_code(db, _hash_phone(body.phone), "phone", body.code)
    return {"verified": True}


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "이미 가입된 이메일입니다")

    phone_hash = _hash_phone(body.phone)
    _check_code(db, body.email, "email", body.email_code)
    _check_code(db, phone_hash, "phone", body.phone_code)

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

    db.execute(delete(SignupVerification).where(SignupVerification.target.in_([body.email, phone_hash])))
    _create_default_store_and_subscription(user, db)
    db.commit()

    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    return TokenResponse(access_token=create_token(user.id), user=_user_dict(user))


@router.post("/kakao/callback")
def kakao_callback(body: KakaoCallbackRequest, db: Session = Depends(get_db)):
    try:
        access_token = exchange_code_for_token(body.code, body.redirect_uri)
        kakao_user = fetch_kakao_user(access_token)
    except KakaoAuthError:
        raise HTTPException(502, "카카오 로그인에 실패했습니다")

    social = db.scalar(
        select(SocialAccount).where(
            SocialAccount.provider == "kakao",
            SocialAccount.provider_user_id == kakao_user.id,
        )
    )
    if social:
        user = db.get(User, social.user_id)
        if user is None:
            raise HTTPException(401, "사용자를 찾을 수 없습니다")
    else:
        user = None
        if kakao_user.email:
            user = db.scalar(select(User).where(User.email == kakao_user.email))
        if user is None:
            user = User(
                email=kakao_user.email,
                password_hash=None,
                nickname=kakao_user.nickname,
                marketing_agreed=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
            db.flush()
            _create_default_store_and_subscription(user, db)
        db.add(SocialAccount(
            user_id=user.id, provider="kakao", provider_user_id=kakao_user.id,
            connected_at=datetime.now(timezone.utc),
        ))

    db.commit()
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
        user.phone_hash = _hash_phone(body.phone)
    if body.marketing_agreed is not None:
        user.marketing_agreed = body.marketing_agreed
    db.commit()
    return _user_dict(user)
