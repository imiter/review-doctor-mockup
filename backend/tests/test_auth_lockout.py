import pytest


@pytest.fixture(autouse=True)
def _clear_login_failures():
    from app.routers import auth as auth_router

    auth_router._login_failures.clear()
    yield
    # 이 파일의 마지막 테스트가 잠금 상태(5회 실패)를 남긴 채 끝나면, 모듈 레벨
    # 딕셔너리가 프로세스 전체에서 유지되는 특성상 이후 실행되는 다른 테스트
    # 파일(예: auth_headers 픽스처로 demo@dris.kr 로그인을 재사용하는 파일들)까지
    # 잠겨서 실패한다. setup만으로는 "이 파일 안에서" 누수만 막을 뿐 "이 파일
    # 다음"으로의 누수는 못 막으므로 teardown에서도 반드시 지운다.
    auth_router._login_failures.clear()


def test_login_locks_out_after_five_failures(client, seeded_user):
    for _ in range(5):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 429


def test_login_success_resets_failure_counter(client, seeded_user):
    for _ in range(4):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 200

    # 성공 직후엔 카운터가 리셋됐으니 다시 4번 실패해도 아직 안 잠긴다
    for _ in range(4):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401
    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    assert res.status_code == 200


def test_login_lockout_is_scoped_to_email(client, seeded_user):
    for _ in range(5):
        res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "wrong-password"})
        assert res.status_code == 401

    # 다른 이메일은 잠기지 않는다(존재하지 않는 계정이라 401이지 429가 아님)
    res = client.post("/auth/login", json={"email": "other@example.com", "password": "whatever"})
    assert res.status_code == 401
