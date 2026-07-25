def test_list_connections_includes_seed_baemin(client, seeded_user, auth_headers):
    res = client.get("/store-connections", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["platform_code"] == "baemin"
    assert body[0]["platform_store_id"] == "MK-1"


def test_connect_new_platform_creates_mock_ids(client, seeded_user, platforms, auth_headers):
    res = client.post("/store-connections", json={"platform_id": platforms["yogiyo"].id}, headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["platform_code"] == "yogiyo"
    assert body["platform_store_id"].startswith("MK-")
    assert body["business_number"]

    listed = client.get("/store-connections", headers=auth_headers).json()
    assert len(listed) == 2


def test_connect_duplicate_platform_rejected(client, seeded_user, platforms, auth_headers):
    res = client.post("/store-connections", json={"platform_id": platforms["baemin"].id}, headers=auth_headers)
    assert res.status_code == 409


def test_disconnect_platform(client, seeded_user, platforms, auth_headers):
    connected = client.post("/store-connections", json={"platform_id": platforms["yogiyo"].id}, headers=auth_headers).json()

    res = client.delete(f"/store-connections/{connected['id']}", headers=auth_headers)
    assert res.status_code == 204

    listed = client.get("/store-connections", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["platform_code"] == "baemin"


def test_disconnect_other_users_connection_forbidden(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timezone

    from app.auth import hash_password
    from app.models import Store, StorePlatformConnection, User

    other = User(email="rival@test.com", password_hash=hash_password("x"), nickname="경쟁사장", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="라이벌가게", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.flush()
    other_conn = StorePlatformConnection(
        store_id=other_store.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-OTHER", business_number="000", connected_at=datetime.now(timezone.utc),
    )
    db_session.add(other_conn)
    db_session.commit()

    res = client.delete(f"/store-connections/{other_conn.id}", headers=auth_headers)
    assert res.status_code == 404
