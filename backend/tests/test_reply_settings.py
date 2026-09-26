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
    from app.models import Subscription
    from datetime import date
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()
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


def test_update_reply_settings_rejects_auto_reply_for_basic(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles)  # 기본 Subscription은 basic (conftest.seeded_user)
    res = client.put("/reply-settings", json={"auto_reply_enabled": True}, headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_update_reply_settings_allows_auto_reply_for_pro(client, db_session, seeded_user, reply_styles, auth_headers):
    from app.models import Subscription
    from datetime import date
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()
    make_settings(db_session, seeded_user["store"], reply_styles)
    res = client.put("/reply-settings", json={"auto_reply_enabled": True}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is True


def test_update_reply_settings_allows_disabling_for_basic(client, db_session, seeded_user, reply_styles, auth_headers):
    """끄는 요청은 플랜과 무관하게 항상 허용돼야 한다 — Pro였다가
    다운그레이드된 사용자도 자기 설정을 끌 수는 있어야 한다."""
    make_settings(db_session, seeded_user["store"], reply_styles, auto_reply_enabled=True)
    res = client.put("/reply-settings", json={"auto_reply_enabled": False}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is False


def test_update_reply_settings_rejects_other_users_store(client, db_session, seeded_user, reply_styles, auth_headers):
    """store_id를 남의 매장으로 지정해도 404여야 한다 — reply_settings에는
    소유권 확인이 없어서 원래는 아무 store_id나 조회/수정할 수 있었다."""
    from datetime import date, datetime, timezone

    from app.auth import hash_password
    from app.models import Store, Subscription, User

    other_user = User(
        email="other@dris.kr", password_hash=hash_password("other1234!"), nickname="박사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()

    other_store = Store(
        user_id=other_user.id, name="다른가게", category="분식",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_store)
    db_session.flush()
    db_session.add(Subscription(user_id=other_user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))
    db_session.commit()

    make_settings(db_session, other_store, reply_styles)

    get_res = client.get("/reply-settings", params={"store_id": other_store.id}, headers=auth_headers)
    assert get_res.status_code == 404

    put_res = client.put(
        "/reply-settings",
        params={"store_id": other_store.id},
        json={"promo_text": "탈취된 문구"},
        headers=auth_headers,
    )
    assert put_res.status_code == 404
