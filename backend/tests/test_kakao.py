import pytest

from app import kakao


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_exchange_code_for_token_returns_access_token(monkeypatch):
    monkeypatch.setattr(kakao, "KAKAO_CLIENT_ID", "test-client-id")

    def fake_post(url, data, timeout):
        assert url == kakao._TOKEN_URL
        assert data["code"] == "auth-code-123"
        assert data["redirect_uri"] == "http://localhost:3000/auth/kakao/callback"
        assert data["client_id"] == "test-client-id"
        return _FakeResponse(200, {"access_token": "kakao-access-token"})

    monkeypatch.setattr(kakao.httpx, "post", fake_post)

    token = kakao.exchange_code_for_token("auth-code-123", "http://localhost:3000/auth/kakao/callback")
    assert token == "kakao-access-token"


def test_exchange_code_for_token_raises_on_failure(monkeypatch):
    monkeypatch.setattr(kakao.httpx, "post", lambda url, data, timeout: _FakeResponse(400, {"error": "invalid_grant"}))

    with pytest.raises(kakao.KakaoAuthError):
        kakao.exchange_code_for_token("bad-code", "http://localhost:3000/auth/kakao/callback")


def test_fetch_kakao_user_parses_nickname_and_email(monkeypatch):
    payload = {
        "id": 123456789,
        "kakao_account": {
            "is_email_valid": True,
            "email": "user@kakao.com",
            "profile": {"nickname": "김사장"},
        },
    }
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(200, payload))

    user = kakao.fetch_kakao_user("kakao-access-token")
    assert user.id == "123456789"
    assert user.nickname == "김사장"
    assert user.email == "user@kakao.com"


def test_fetch_kakao_user_without_email_consent_returns_none_email(monkeypatch):
    payload = {"id": 999, "kakao_account": {"profile": {"nickname": "미인증사장"}}}
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(200, payload))

    user = kakao.fetch_kakao_user("kakao-access-token")
    assert user.id == "999"
    assert user.nickname == "미인증사장"
    assert user.email is None


def test_fetch_kakao_user_raises_on_failure(monkeypatch):
    monkeypatch.setattr(kakao.httpx, "get", lambda url, headers, timeout: _FakeResponse(401, {}))

    with pytest.raises(kakao.KakaoAuthError):
        kakao.fetch_kakao_user("expired-token")
