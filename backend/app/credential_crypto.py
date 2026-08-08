"""배민 등 실계정 자격증명을 Fernet 대칭키로 암호화/복호화한다.

원문 ID/PW는 로그에 남기지 않는다 — 카카오 시크릿·Resend 키와 동일하게
환경변수(CREDENTIAL_ENCRYPTION_KEY)로만 다룬다.
"""

import json
import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialCryptoError(Exception):
    pass


def _get_fernet() -> Fernet:
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise CredentialCryptoError("CREDENTIAL_ENCRYPTION_KEY 환경변수가 설정되지 않았습니다")
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise CredentialCryptoError("CREDENTIAL_ENCRYPTION_KEY 형식이 올바르지 않습니다") from e


def encrypt_credential(login_id: str, password: str) -> str:
    payload = json.dumps({"login_id": login_id, "password": password}).encode()
    return _get_fernet().encrypt(payload).decode()


def decrypt_credential(ciphertext: str) -> dict[str, str]:
    try:
        payload = _get_fernet().decrypt(ciphertext.encode())
    except InvalidToken as e:
        raise CredentialCryptoError("자격증명 복호화 실패") from e
    return json.loads(payload)
