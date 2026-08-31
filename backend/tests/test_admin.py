from datetime import datetime, timezone

from app.models import User


def test_user_role_defaults_to_owner(db_session):
    user = User(nickname="테스트", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.role == "owner"


def test_auth_me_includes_role(client, seeded_user, auth_headers):
    res = client.get("/auth/me", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["role"] == "owner"
