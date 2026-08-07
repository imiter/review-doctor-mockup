import httpx
import pytest

from app import email_verification as ev


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_generate_code_is_six_digits():
    code = ev.generate_code()
    assert len(code) == 6
    assert code.isdigit()


def test_hash_code_is_deterministic_sha256():
    assert ev.hash_code("123456") == ev.hash_code("123456")
    assert ev.hash_code("123456") != ev.hash_code("654321")
    assert len(ev.hash_code("123456")) == 64


def test_send_verification_email_success(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    def fake_post(url, headers, json, timeout):
        assert url == ev._RESEND_URL
        assert headers["Authorization"] == "Bearer test-key"
        assert json["to"] == "user@example.com"
        assert "482913" in json["html"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913")  # 예외 없이 통과하면 성공


def test_send_verification_email_raises_on_api_error(monkeypatch):
    monkeypatch.setattr(ev.httpx, "post", lambda url, headers, json, timeout: _FakeResponse(422))

    with pytest.raises(ev.EmailSendError):
        ev.send_verification_email("user@example.com", "482913")


def test_send_verification_email_wraps_network_error(monkeypatch):
    def _raise(url, headers, json, timeout):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(ev.httpx, "post", _raise)

    with pytest.raises(ev.EmailSendError):
        ev.send_verification_email("user@example.com", "482913")


def test_send_verification_email_password_reset_uses_different_subject(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["subject"] = json["subject"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913", purpose="password_reset")
    assert captured["subject"] == "[Delivery Review] 비밀번호 재설정 인증번호"


def test_send_verification_email_defaults_to_signup_subject(monkeypatch):
    monkeypatch.setattr(ev, "RESEND_API_KEY", "test-key")

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["subject"] = json["subject"]
        return _FakeResponse(200)

    monkeypatch.setattr(ev.httpx, "post", fake_post)
    ev.send_verification_email("user@example.com", "482913")
    assert captured["subject"] == "[Delivery Review] 이메일 인증번호"
