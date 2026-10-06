from datetime import date, datetime, timezone

import pytest

from app.models import Alert, DailySettlement, Order, RepurchaseMetric, Review


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


def test_dashboard_platform_id_filters_sales_and_deposit_today(client, db_session, seeded_user, platforms, auth_headers):
    """platform_id를 지정하면 오늘 매출/입금이 그 플랫폼만 집계돼야 한다 —
    지정 안 하면(기존 동작) 모든 플랫폼(요기요/쿠팡이츠 Mock 포함)이 합산된다."""
    today = date.today()
    db_session.add_all([
        DailySettlement(store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
                         settle_date=today, sales_amount=50000, deposit_amount=40000),
        DailySettlement(store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id,
                         settle_date=today, sales_amount=30000, deposit_amount=20000),
    ])
    db_session.commit()

    body_all = client.get("/dashboard", headers=auth_headers).json()
    assert body_all["sales_today"] == 80000
    assert body_all["deposit_today"] == 60000

    body_baemin = client.get(
        f"/dashboard?platform_id={platforms['baemin'].id}", headers=auth_headers
    ).json()
    assert body_baemin["sales_today"] == 50000
    assert body_baemin["deposit_today"] == 40000


def test_dashboard_platform_id_filters_latest_repurchase_rate(client, db_session, seeded_user, platforms, auth_headers):
    """platform_id를 지정하면 그 플랫폼의 최신 재주문율만 반환해야 한다 —
    지정 안 하면 다른 플랫폼의 더 최근 날짜 행이 섞여 들어올 수 있다."""
    db_session.add_all([
        RepurchaseMetric(store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
                          metric_date=date(2026, 7, 20), new_orders=10, repeat_orders=2, rate_raw="0.2000", rate_adjusted="0.2000"),
        RepurchaseMetric(store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id,
                          metric_date=date(2026, 7, 25), new_orders=10, repeat_orders=5, rate_raw="0.5000", rate_adjusted="0.5000"),
    ])
    db_session.commit()

    body_baemin = client.get(
        f"/dashboard?platform_id={platforms['baemin'].id}", headers=auth_headers
    ).json()
    assert body_baemin["repurchase_rate_adjusted"] == 0.2  # 요기요의 더 최근 값(0.5)이 섞이면 안 됨


def test_dashboard_counts_unread_alerts_only(client, db_session, seeded_user, auth_headers):
    db_session.add_all([
        Alert(store_id=seeded_user["store"].id, alert_type="negative_review", message="a", is_read=False, created_at=datetime.now(timezone.utc)),
        Alert(store_id=seeded_user["store"].id, alert_type="negative_review", message="b", is_read=True, created_at=datetime.now(timezone.utc)),
    ])
    db_session.commit()

    body = client.get("/dashboard", headers=auth_headers).json()
    assert body["unread_alerts"] == 1


def test_draft_feedback_trend_aggregates_by_category(client, auth_headers, db_session, seeded_user):
    from app.models import DraftFeedbackScore

    store = seeded_user["store"]
    db_session.add_all([
        DraftFeedbackScore(
            store_id=store.id, category="food_quality", similarity_score=0.9,
            trace_id="d1111111-1111-1111-1111-111111111111",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=store.id, category="food_quality", similarity_score=0.7,
            trace_id="d2222222-2222-2222-2222-222222222222",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=store.id, category="no_issue", similarity_score=0.95,
            trace_id="d3333333-3333-3333-3333-333333333333",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    resp = client.get(f"/dashboard/draft-feedback-trend?store_id={store.id}", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    by_category = {c["category"]: c for c in body["categories"]}
    assert by_category["food_quality"]["sample_count"] == 2
    assert by_category["food_quality"]["avg_similarity"] == pytest.approx(0.8, abs=0.001)
    assert by_category["food_quality"]["label"] == "음식 품질(맛/온도/양)"
    assert by_category["no_issue"]["sample_count"] == 1
    assert by_category["no_issue"]["label"] == "특이 불만 없음"


def test_draft_feedback_trend_empty_when_no_scores(client, auth_headers, seeded_user):
    store = seeded_user["store"]
    resp = client.get(f"/dashboard/draft-feedback-trend?store_id={store.id}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"categories": []}
