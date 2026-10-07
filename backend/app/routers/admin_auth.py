"""admin 전용 로그인 — 사장님 회원(users 테이블) 체계와 완전히 무관하다.
단일 비밀번호(ADMIN_PASSWORD)만 검증하고, 성공하면 admin 전용 시크릿
(ADMIN_JWT_SECRET)으로 서명한 JWT를 돌려준다. DB 조회가 전혀 없다.

두 값 모두 미설정 시 기본값으로 폴백하지 않는다(fail-closed) — JWT_SECRET과
다르다. JWT_SECRET이 유출돼도 공격자는 여전히 실제 users 행이 있어야
뭔가를 할 수 있지만, ADMIN_PASSWORD는 그 자체로 완결된 자격증명이라 레포에
박힌 기본값이 그대로 배포되면(크롤 워커처럼 같은 app.main:app을 띄우는
프로세스 포함) 누구든 admin 패널 전체에 들어갈 수 있다. 그래서 둘 중
하나라도 없으면 503으로 거부한다(최종 리뷰에서 지적받아 번복, 아래
CLAUDE.md "관리자 패널 완전 분리" 절 참고)."""

import hmac
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET")
ADMIN_JWT_ALGORITHM = "HS256"
ADMIN_JWT_EXPIRE_HOURS = 24 * 7

router = APIRouter(tags=["admin-auth"])
_admin_bearer = HTTPBearer(auto_error=False)

# auth.py의 로그인 lockout 패턴과 동일(이메일 대신 클라이언트 IP로 키).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_WINDOW = timedelta(minutes=15)
_login_failures: dict[str, list[datetime]] = defaultdict(list)


def _check_login_lockout(client_ip: str) -> None:
    now = datetime.now(timezone.utc)
    recent = [t for t in _login_failures.get(client_ip, []) if now - t < _LOGIN_LOCKOUT_WINDOW]
    if recent:
        _login_failures[client_ip] = recent
    else:
        _login_failures.pop(client_ip, None)
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(429, "너무 많이 실패했어요. 15분 후 다시 시도해주세요")


def _record_login_failure(client_ip: str) -> None:
    _login_failures[client_ip].append(datetime.now(timezone.utc))
    if len(_login_failures) > 10_000:
        now = datetime.now(timezone.utc)
        for key in list(_login_failures.keys()):
            recent = [t for t in _login_failures[key] if now - t < _LOGIN_LOCKOUT_WINDOW]
            if recent:
                _login_failures[key] = recent
            else:
                del _login_failures[key]


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
def admin_login(body: AdminLoginRequest, request: Request):
    if not ADMIN_PASSWORD or not ADMIN_JWT_SECRET:
        raise HTTPException(503, "admin 로그인이 설정되지 않았습니다")
    client_ip = request.client.host if request.client else "unknown"
    if hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        return AdminLoginResponse(access_token=create_admin_token())
    _check_login_lockout(client_ip)
    _record_login_failure(client_ip)
    raise HTTPException(401, "비밀번호가 올바르지 않습니다")


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_admin_bearer),
) -> None:
    """admin 전용 라우트에서 쓰는 FastAPI dependency. ADMIN_JWT_SECRET으로만
    검증하고 DB 조회가 없다 — admin이 User 레코드가 아니기 때문이다."""
    if not ADMIN_JWT_SECRET:
        raise HTTPException(503, "admin 로그인이 설정되지 않았습니다")
    if credentials is None:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        payload = jwt.decode(credentials.credentials, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "유효하지 않은 토큰입니다")
    if not payload.get("admin"):
        raise HTTPException(401, "관리자 권한이 필요합니다")
    return None
