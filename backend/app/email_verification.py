"""이메일 회원가입 인증 — 6자리 코드 생성·해시 + Resend 실발송."""

import hashlib
import os
import secrets
from datetime import timedelta

import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "onboarding@resend.dev")

_RESEND_URL = "https://api.resend.com/emails"

EMAIL_CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


class EmailSendError(Exception):
    pass


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def send_verification_email(to: str, code: str) -> None:
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": EMAIL_FROM_ADDRESS,
                "to": to,
                "subject": "[Delivery Review] 이메일 인증번호",
                "html": f"<p>인증번호: <b>{code}</b> (10분 이내 입력해주세요)</p>",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise EmailSendError(f"이메일 발송 요청 실패: {e}") from e
    if resp.status_code >= 400:
        raise EmailSendError(f"이메일 발송 실패: {resp.status_code}")
