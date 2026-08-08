from datetime import date, datetime, timezone

from app.models import Alert, Order, RepurchaseMetric, Review


def test_dashboard_counts_unanswered_reviews(client, db_session, seeded_user, platforms, auth_headers):
    order = Order(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, order_no="D-1",
        ordered_at=datetime.now(timezone.utc), menu_summary="치킨", order_type="delivery", amount=20000,
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(Review(
        order_id=order.id, store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="별로", customer_nickname="익명", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    body = client.get("/dashboard", headers=auth_headers).json()
    assert body["unanswered_reviews"] == 1
    assert body["ad_performance"] is None  # 캠페인 없으면 null


def test_dashboard_counts_unanswered_reviews_without_order(client, db_session, seeded_user, platforms, auth_headers):
    """order_id 없이(배민 스크래핑처럼) 적재된 미답변 리뷰도 대시보드 집계에 포함되는지 확인."""
    db_session.add(Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="양념치킨", rating=2, content="배송이 늦었어요", customer_nickname="익명2",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    body = client.get("/dashboard", headers=auth_headers).json()
    assert body["unanswered_reviews"] == 1


def test_dashboard_shows_latest_repurchase_rate(client, db_session, seeded_user, platforms, auth_headers):
    db_session.add_all([
        RepurchaseMetric(store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
                          metric_date=date(2026, 7, 20), new_orders=10, repeat_orders=2, rate_raw="0.1667", rate_adjusted="0.1500"),
        RepurchaseMetric(store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
                          metric_date=date(2026, 7, 24), new_orders=10, repeat_orders=3, rate_raw="0.2308", rate_adjusted="0.1900"),
    ])
    db_session.commit()

    body = client.get("/dashboard", headers=auth_headers).json()
    assert body["repurchase_rate_adjusted"] == 0.19  # 가장 최근 날짜 값


def test_dashboard_counts_unread_alerts_only(client, db_session, seeded_user, auth_headers):
    db_session.add_all([
        Alert(store_id=seeded_user["store"].id, alert_type="negative_review", message="a", is_read=False, created_at=datetime.now(timezone.utc)),
        Alert(store_id=seeded_user["store"].id, alert_type="negative_review", message="b", is_read=True, created_at=datetime.now(timezone.utc)),
    ])
    db_session.commit()

    body = client.get("/dashboard", headers=auth_headers).json()
    assert body["unread_alerts"] == 1
