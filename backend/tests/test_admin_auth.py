import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.routers import admin_auth


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setattr(admin_auth, "ADMIN_JWT_SECRET", "test-admin-secret")


@pytest.fixture(autouse=True)
def _reset_login_failures():
    """_login_failures는 모듈 레벨 dict라 테스트 프로세스 안에서 테스트 함수
    경계를 넘어 상태가 남는다 — 한 테스트의 lockout이 다음 테스트로 새지
    않도록 매 테스트 전에 비운다."""
    admin_auth._login_failures.clear()


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_auth.router)

    @app.get("/protected")
    def protected(_: None = Depends(admin_auth.require_admin_token)):
        return {"ok": True}

    return app


@pytest.fixture()
def admin_client():
    return TestClient(_make_test_app())


def test_login_with_correct_password_returns_token(admin_client):
    res = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"})
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_login_with_wrong_password_returns_401(admin_client):
    res = admin_client.post("/admin-auth/login", json={"password": "wrong"})
    assert res.status_code == 401


def test_protected_route_accepts_valid_admin_token(admin_client):
    token = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"}).json()["access_token"]
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_protected_route_rejects_missing_token(admin_client):
    res = admin_client.get("/protected")
    assert res.status_code == 401


def test_protected_route_rejects_token_signed_with_wrong_secret(admin_client):
    forged = jwt.encode({"admin": True}, "not-the-real-secret", algorithm="HS256")
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_protected_route_rejects_token_without_admin_claim(admin_client):
    forged = jwt.encode({"admin": False}, "test-admin-secret", algorithm="HS256")
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_admin_login_endpoint_is_registered_on_real_app(client, monkeypatch):
    """실제 FastAPI 앱(main.py)에도 라우터가 등록돼 있는지 확인하는 통합
    테스트 — client fixture는 backend/tests/conftest.py가 제공한다."""
    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", "real-app-test-pw")
    res = client.post("/admin-auth/login", json={"password": "real-app-test-pw"})
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_login_returns_503_when_password_unset(admin_client, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PASSWORD", None)
    res = admin_client.post("/admin-auth/login", json={"password": "anything"})
    assert res.status_code == 503


def test_login_returns_503_when_jwt_secret_unset(admin_client, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_JWT_SECRET", None)
    res = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"})
    assert res.status_code == 503


def test_protected_route_returns_503_when_jwt_secret_unset(admin_client, monkeypatch):
    token = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"}).json()["access_token"]
    monkeypatch.setattr(admin_auth, "ADMIN_JWT_SECRET", None)
    res = admin_client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 503


def test_sixth_wrong_password_attempt_is_locked_out(admin_client):
    for _ in range(5):
        res = admin_client.post("/admin-auth/login", json={"password": "wrong"})
        assert res.status_code == 401
    res = admin_client.post("/admin-auth/login", json={"password": "wrong"})
    assert res.status_code == 429


def test_correct_password_succeeds_despite_prior_failures_below_cap(admin_client):
    for _ in range(3):
        res = admin_client.post("/admin-auth/login", json={"password": "wrong"})
        assert res.status_code == 401
    res = admin_client.post("/admin-auth/login", json={"password": "test-admin-pw"})
    assert res.status_code == 200
    assert "access_token" in res.json()
