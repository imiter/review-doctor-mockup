from datetime import datetime, timedelta, timezone

from app.models import Order


def make_order(db_session, store, platform, days_ago, amount=15000):
    order = Order(
        store_id=store.id, platform_id=platform.id,
        order_no=f"T-{platform.code}-{days_ago}", ordered_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        menu_summary="후라이드", order_type="delivery", amount=amount,
    )
    db_session.add(order)
    db_session.commit()
    return order


def test_orders_scoped_to_default_store(client, db_session, seeded_user, platforms, auth_headers):
    make_order(db_session, seeded_user["store"], platforms["baemin"], days_ago=1)
    res = client.get("/orders", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["platform_name"] == "배달의민족"


def test_orders_filter_by_platform(client, db_session, seeded_user, platforms, auth_headers):
    from app.models import StorePlatformConnection

    db_session.add(StorePlatformConnection(
        store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id,
        platform_store_id="MK-Y", business_number="000", connected_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    make_order(db_session, seeded_user["store"], platforms["baemin"], days_ago=1)
    make_order(db_session, seeded_user["store"], platforms["yogiyo"], days_ago=1)

    res = client.get(f"/orders?platform_id={platforms['yogiyo'].id}", headers=auth_headers)
    assert len(res.json()) == 1
    assert res.json()[0]["platform_name"] == "요기요"


def test_orders_filter_by_date_range(client, db_session, seeded_user, platforms, auth_headers):
    make_order(db_session, seeded_user["store"], platforms["baemin"], days_ago=10)
    make_order(db_session, seeded_user["store"], platforms["baemin"], days_ago=1)

    from_date = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    res = client.get(f"/orders?from_date={from_date}", headers=auth_headers)
    assert len(res.json()) == 1
