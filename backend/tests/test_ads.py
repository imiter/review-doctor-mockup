from datetime import date, datetime, timezone

from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, Order


def make_campaign(db_session, store, current_cpc=400, target_rank=3):
    campaign = AdCampaign(store_id=store.id, category="치킨", current_cpc=current_cpc, target_rank=target_rank, status="active")
    db_session.add(campaign)
    db_session.commit()
    return campaign


def test_ads_performance_computes_acos_from_raw_metrics(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=169_000, clicks=1000, ad_orders=184, ad_revenue=184 * 25_000,
    ))
    db_session.commit()

    res = client.get("/ads/performance", headers=auth_headers)
    assert res.status_code == 200
    row = res.json()[0]
    assert row["cpc"] == 169.0
    assert row["cvr"] == 0.184
    assert row["acos"] is not None
    assert row["score"] is not None


def test_ads_performance_aggregates_multiple_days(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add_all([
        AdPerformanceMetric(campaign_id=campaign.id, metric_date=date.today(), ad_spend=10_000, clicks=100, ad_orders=10, ad_revenue=200_000),
        AdPerformanceMetric(campaign_id=campaign.id, metric_date=date.today(), ad_spend=15_000, clicks=150, ad_orders=15, ad_revenue=300_000),
    ])
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 25_000
    assert row["clicks"] == 250
    assert row["ad_orders"] == 25


def test_ads_performance_order_share(client, db_session, seeded_user, platforms, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(), ad_spend=10_000, clicks=100, ad_orders=20, ad_revenue=400_000,
    ))
    for i in range(100):  # 매장 전체 주문 100건 (order_share 분모) — ad_orders 20건은 AdPerformanceMetric 집계값
        db_session.add(Order(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, order_no=f"OS-{i}",
            ordered_at=datetime.now(timezone.utc), menu_summary="치킨", order_type="delivery", amount=15_000,
        ))
    db_session.commit()

    row = client.get("/ads/performance?days=14", headers=auth_headers).json()[0]
    assert row["order_share"] == 0.2  # 20 / 100


def test_ads_performance_order_share_none_when_no_orders(client, db_session, seeded_user, auth_headers):
    make_campaign(db_session, seeded_user["store"])
    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["order_share"] is None


def test_rank_monitoring_returns_latest_snapshot_and_recommendation(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc),
                        current_rank=3, competitor_est_cpc=390, status="normal", recommended_action="keep"),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
                        current_rank=7, competitor_est_cpc=650, status="rank_dropped",
                        recommended_action="raise_cpc", suggested_cpc=700),
    ])
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 7  # 가장 최신 스냅샷
    assert row["rank_status"] == "rank_dropped"
    assert row["suggested_cpc"] == 700


def test_rank_monitoring_no_snapshot_yet(client, db_session, seeded_user, auth_headers):
    make_campaign(db_session, seeded_user["store"])
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] is None
    assert row["recommended_action"] == "keep"


def test_rank_by_distance_returns_points_sorted_by_distance(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                        current_rank=17, distance_km="0.00", point_label="0km", total_scanned=17, ads_above=4),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 1, tzinfo=timezone.utc),
                        current_rank=9, distance_km="2.37", point_label="1.5~2.5km", total_scanned=10, ads_above=3),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 2, tzinfo=timezone.utc),
                        current_rank=17, distance_km="2.61", point_label="2.5~3.5km", total_scanned=17, ads_above=5),
    ])
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert [p["point_label"] for p in row["points"]] == ["0km", "1.5~2.5km", "2.5~3.5km"]
    assert row["points"][1]["current_rank"] == 9


def test_rank_by_distance_keeps_only_latest_snapshot_per_point(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
                        current_rank=20, distance_km="0.00", point_label="0km", total_scanned=20, ads_above=4),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                        current_rank=17, distance_km="0.00", point_label="0km", total_scanned=17, ads_above=4),
    ])
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert len(row["points"]) == 1
    assert row["points"][0]["current_rank"] == 17  # 가장 최신 값만 남는다


def test_rank_by_distance_ignores_time_series_mock_rows(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc),
        current_rank=3, competitor_est_cpc=390, status="normal", recommended_action="keep",
    ))
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert row["points"] == []
