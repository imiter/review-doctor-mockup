"""이메일 회원가입 인증 — 6자리 코드 생성·해시 + Resend 실발송.

휴대폰 인증은 이 모듈에서 발송 함수를 제공하지 않는다 — CLAUDE.md 원칙상 실제
SMS 발송이 금지돼 있어 Mock이다. 라우터가 generate_code()로 코드를 만들어 API
응답에 그대로 돌려주고, 실제로는 아무 곳에도 전송하지 않는다.
"""

import hashlib
import os
import secrets
from datetime import timedelta

import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "onboarding@resend.dev")

_RESEND_URL = "https://api.resend.com/emails"

EMAIL_CODE_TTL = timedelta(minutes=10)
PHONE_CODE_TTL = timedelta(minutes=5)
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
