"""카카오 로그인 — 인가 코드를 access_token으로 교환하고 사용자 정보를 조회한다."""

import os
from dataclasses import dataclass

import httpx

KAKAO_CLIENT_ID = os.getenv("KAKAO_CLIENT_ID", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")  # 이 프로젝트의 카카오 앱은 Client Secret이 필수다 (없으면 invalid_client/KOE010)

_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
_USER_URL = "https://kapi.kakao.com/v2/user/me"


class KakaoAuthError(Exception):
    pass


@dataclass
class KakaoUser:
    id: str
    nickname: str
    email: str | None


def exchange_code_for_token(code: str, redirect_uri: str) -> str:
    data = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET

    try:
        resp = httpx.post(_TOKEN_URL, data=data, timeout=10.0)
    except httpx.HTTPError as e:
        raise KakaoAuthError(f"카카오 토큰 요청 실패: {e}") from e
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 토큰 교환 실패: {resp.status_code}")
    token = resp.json().get("access_token")
    if not token:
        raise KakaoAuthError("카카오 응답에 access_token이 없습니다")
    return token


def fetch_kakao_user(access_token: str) -> KakaoUser:
    try:
        resp = httpx.get(_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0)
    except httpx.HTTPError as e:
        raise KakaoAuthError(f"카카오 사용자 조회 요청 실패: {e}") from e
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 사용자 조회 실패: {resp.status_code}")

    body = resp.json()
    user_id = body.get("id")
    if user_id is None:
        raise KakaoAuthError("카카오 응답에 사용자 id가 없습니다")

    account = body.get("kakao_account", {})
    profile = account.get("profile", {})
    nickname = profile.get("nickname") or body.get("properties", {}).get("nickname") or "카카오사용자"
    email = account.get("email") if account.get("is_email_valid") and account.get("is_email_verified") else None
    return KakaoUser(id=str(user_id), nickname=nickname, email=email)
