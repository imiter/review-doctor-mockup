import httpx
import pytest

from app import toss_client


def test_confirm_payment_missing_secret_key_raises(monkeypatch):
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    with pytest.raises(toss_client.TossConfirmError, match="TOSS_SECRET_KEY"):
        toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)


def test_confirm_payment_success(monkeypatch):
    monkeypatch.setenv("TOSS_SECRET_KEY", "test_sk_dummy")

    def fake_post(url, json, headers, timeout):
        assert url == "https://api.tosspayments.com/v1/payments/confirm"
        assert json == {"paymentKey": "pk", "orderId": "oid", "amount": 19900}
        assert headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"status": "DONE", "orderId": "oid"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(toss_client.httpx, "post", fake_post)
    result = toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)
    assert result["status"] == "DONE"


def test_confirm_payment_toss_rejects(monkeypatch):
    monkeypatch.setenv("TOSS_SECRET_KEY", "test_sk_dummy")

    def fake_post(url, json, headers, timeout):
        return httpx.Response(
            400, json={"code": "REJECT_CARD_COMPANY", "message": "카드사에서 결제를 거절했습니다"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(toss_client.httpx, "post", fake_post)
    with pytest.raises(toss_client.TossConfirmError, match="카드사에서 결제를 거절했습니다"):
        toss_client.confirm_payment(payment_key="pk", order_id="oid", amount=19900)
