from datetime import date, datetime, timezone

from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot


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
