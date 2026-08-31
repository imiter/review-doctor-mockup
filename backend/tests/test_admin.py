from datetime import datetime, timezone

from app.models import User


def test_user_role_defaults_to_owner(db_session):
    user = User(nickname="테스트", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.role == "owner"
