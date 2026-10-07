"""admin 전용 로그인 — 사장님 회원(users 테이블) 체계와 완전히 무관하다.
단일 비밀번호(ADMIN_PASSWORD)만 검증하고, 성공하면 admin 전용 시크릿
(ADMIN_JWT_SECRET)으로 서명한 JWT를 돌려준다. DB 조회가 전혀 없다."""

import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "dev-admin-password-change-in-production")
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "dev-admin-secret-change-in-production-min-32-bytes")
ADMIN_JWT_ALGORITHM = "HS256"
ADMIN_JWT_EXPIRE_HOURS = 24 * 7

router = APIRouter(tags=["admin-auth"])
_admin_bearer = HTTPBearer(auto_error=False)


class AdminLoginRequest(BaseModel):
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str


def create_admin_token() -> str:
    payload = {
        "admin": True,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ADMIN_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)


@router.post("/admin-auth/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginRequest):
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "비밀번호가 올바르지 않습니다")
    return AdminLoginResponse(access_token=create_admin_token())


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_admin_bearer),
) -> None:
    """admin 전용 라우트에서 쓰는 FastAPI dependency. ADMIN_JWT_SECRET으로만
    검증하고 DB 조회가 없다 — admin이 User 레코드가 아니기 때문이다."""
    if credentials is None:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        payload = jwt.decode(credentials.credentials, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "유효하지 않은 토큰입니다")
    if not payload.get("admin"):
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return None
