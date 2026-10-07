from datetime import datetime, timezone

from app.models import StoreStyleProfile


def test_list_principles_returns_only_unconfirmed(client, auth_headers, db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add_all([
        StoreStyleProfile(
            store_id=sid, category="delivery", rules="배달 원칙", generated_from_count=3,
            needs_confirmation=True, updated_at=datetime.now(timezone.utc),
        ),
        StoreStyleProfile(
            store_id=sid, category="hygiene", rules="위생 원칙", generated_from_count=2,
            needs_confirmation=False, updated_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    resp = client.get(f"/style-principles?store_id={sid}", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["principles"]) == 1
    assert body["principles"][0]["category"] == "delivery"
    assert body["principles"][0]["rules"] == "배달 원칙"
    assert body["principles"][0]["label"] == "배달(지연/파손)"


def test_list_principles_empty_when_nothing_needs_confirmation(client, auth_headers, seeded_user):
    sid = seeded_user["store"].id
    resp = client.get(f"/style-principles?store_id={sid}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"principles": []}


def test_confirm_principle_clears_flag_and_updates_rules(client, auth_headers, db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="delivery", rules="AI가 뽑은 원칙", generated_from_count=3,
        needs_confirmation=True, updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    resp = client.post(
        f"/style-principles/delivery/confirm?store_id={sid}",
        json={"rules": "사장님이 고친 원칙"}, headers=auth_headers,
    )

    assert resp.status_code == 200
    profile = db_session.get(StoreStyleProfile, (sid, "delivery"))
    assert profile.rules == "사장님이 고친 원칙"
    assert profile.needs_confirmation is False


def test_confirm_principle_404_when_no_such_category(client, auth_headers, seeded_user):
    sid = seeded_user["store"].id
    resp = client.post(
        f"/style-principles/delivery/confirm?store_id={sid}",
        json={"rules": "아무거나"}, headers=auth_headers,
    )

    assert resp.status_code == 404


def test_list_principles_404_for_other_users_store(client, auth_headers, db_session, seeded_user):
    from app.models import Store, User

    other_user = User(email="other@example.com", nickname="다른사장", created_at=datetime.now(timezone.utc))
    db_session.add(other_user)
    db_session.flush()
    other_store = Store(
        user_id=other_user.id, name="다른 가게", category="한식",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_store)
    db_session.commit()

    resp = client.get(f"/style-principles?store_id={other_store.id}", headers=auth_headers)

    assert resp.status_code == 404
