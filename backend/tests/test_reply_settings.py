from app.models import ReplySetting


def make_settings(db_session, store, style, **overrides):
    defaults = dict(
        store_id=store.id, style_id=style.id, promo_text="원조 문구",
        include_nickname=True, include_menu=True, include_store_name=True,
        promo_on_negative=False, auto_reply_enabled=False, auto_reply_min_rating=1,
    )
    defaults.update(overrides)
    rs = ReplySetting(**defaults)
    db_session.add(rs)
    db_session.commit()
    return rs


def test_get_reply_settings_returns_current_values(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles, auto_reply_enabled=True, auto_reply_min_rating=4)

    res = client.get("/reply-settings", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["auto_reply_enabled"] is True
    assert body["auto_reply_min_rating"] == 4
    assert body["promo_text"] == "원조 문구"


def test_get_reply_settings_404_when_missing(client, seeded_user, auth_headers):
    res = client.get("/reply-settings", headers=auth_headers)
    assert res.status_code == 404


def test_update_reply_settings_partial(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles)

    res = client.put("/reply-settings", json={"auto_reply_enabled": True, "auto_reply_min_rating": 5}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["auto_reply_enabled"] is True
    assert body["auto_reply_min_rating"] == 5
    assert body["promo_text"] == "원조 문구"  # 건드리지 않은 필드는 유지


def test_update_reply_settings_rejects_out_of_range_rating(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles)
    res = client.put("/reply-settings", json={"auto_reply_min_rating": 6}, headers=auth_headers)
    assert res.status_code == 422


def test_update_reply_settings_style_and_promo(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles)
    res = client.put(
        "/reply-settings",
        json={"promo_text": "새 홍보 문구", "promo_on_negative": True},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["promo_text"] == "새 홍보 문구"
    assert body["promo_on_negative"] is True
