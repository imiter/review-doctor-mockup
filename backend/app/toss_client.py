"""토스페이먼츠 결제 승인(confirm) API 래퍼. 테스트 키(test_sk_...)로만 쓴다."""

import base64
import os

import httpx

_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


class TossConfirmError(Exception):
    pass


class TossTransportError(TossConfirmError):
    """토스에게 물어보지도 못한 상황(네트워크 타임아웃, 시크릿키 미설정 등).
    토스가 명시적으로 거절한 것과 달리 재시도하면 성공할 수 있으므로,
    호출부(billing.confirm)는 이 예외를 TossConfirmError와 다르게 다뤄야 한다
    (payment.status를 "failed"로 확정하지 않고 pending으로 남겨 재시도 가능하게 함)."""


def confirm_payment(payment_key: str, order_id: str, amount: int) -> dict:
    secret_key = os.environ.get("TOSS_SECRET_KEY", "")
    if not secret_key:
        raise TossTransportError("TOSS_SECRET_KEY가 설정되지 않았습니다")

    auth = base64.b64encode(f"{secret_key}:".encode()).decode()
    try:
        res = httpx.post(
            _CONFIRM_URL,
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            headers={"Authorization": f"Basic {auth}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise TossTransportError(f"토스 API 호출 실패: {e}") from e

    if res.status_code != 200:
        try:
            body = res.json()
        except ValueError:
            body = {}
        raise TossConfirmError(body.get("message", f"토스 승인 실패 (status={res.status_code})"))

    return res.json()
