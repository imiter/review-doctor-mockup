"""카카오 로그인 — 인가 코드를 access_token으로 교환하고 사용자 정보를 조회한다."""

import os
from dataclasses import dataclass

import httpx

KAKAO_CLIENT_ID = os.getenv("KAKAO_CLIENT_ID", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")  # 콘솔에서 활성화한 경우에만 사용

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

    resp = httpx.post(_TOKEN_URL, data=data, timeout=10.0)
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 토큰 교환 실패: {resp.status_code}")
    return resp.json()["access_token"]


def fetch_kakao_user(access_token: str) -> KakaoUser:
    resp = httpx.get(_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0)
    if resp.status_code != 200:
        raise KakaoAuthError(f"카카오 사용자 조회 실패: {resp.status_code}")

    body = resp.json()
    account = body.get("kakao_account", {})
    profile = account.get("profile", {})
    nickname = profile.get("nickname") or body.get("properties", {}).get("nickname") or "카카오사용자"
    email = account.get("email") if account.get("is_email_valid", True) else None
    return KakaoUser(id=str(body["id"]), nickname=nickname, email=email)
