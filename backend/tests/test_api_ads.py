from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation, MockClock
from tests.test_models_reviews import make_sp

T0 = datetime(2026, 7, 25, 9, 0)


def setup_campaign(db_session):
    sp = make_sp(db_session)
    campaign = AdCampaign(store_platform_id=sp.id, category="치킨", current_cpc=400, target_rank=3, status="active")
    db_session.add(campaign)
    db_session.flush()
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=T0, my_rank=3, competitor_est_cpc=390),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=T0 + timedelta(minutes=10), my_rank=5, competitor_est_cpc=650),
    ])
    db_session.add(MockClock(id=1, mock_now=T0))
    db_session.commit()
    return campaign


def test_dashboard_uses_visible_snapshot(client, db_session):
    setup_campaign(db_session)
    body = client.get("/api/ad-campaigns").json()
    assert body["mock_now"] == T0.isoformat()
    row = body["campaigns"][0]
    assert row["my_rank"] == 3  # 미래 스냅샷(5위)은 아직 안 보임
    assert row["recommendation"] is None


def test_refresh_advances_and_recommends(client, db_session):
    campaign = setup_campaign(db_session)
    res = client.post("/api/ads/refresh")
    assert res.json()["mock_now"] == (T0 + timedelta(minutes=10)).isoformat()

    rec = db_session.scalars(select(AdRecommendation)).one()
    assert rec.action_type == "raise_cpc"
    assert rec.suggested_cpc == 650 + 50
    assert rec.status == "pending"

    client.post("/api/ads/refresh")  # pending 존재 → 중복 생성 금지
    assert len(db_session.scalars(select(AdRecommendation)).all()) == 1


def test_apply_records_history(client, db_session):
    campaign = setup_campaign(db_session)
    client.post("/api/ads/refresh")
    rec = db_session.scalars(select(AdRecommendation)).one()

    res = client.post(f"/api/ad-recommendations/{rec.id}/apply")
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(AdCampaign, campaign.id).current_cpc == 700
    hist = db_session.scalars(select(AdBidHistory)).one()
    assert (hist.old_cpc, hist.new_cpc, hist.recommendation_id) == (400, 700, rec.id)

    assert client.post(f"/api/ad-recommendations/{rec.id}/apply").status_code == 409


def test_dismiss(client, db_session):
    setup_campaign(db_session)
    client.post("/api/ads/refresh")
    rec = db_session.scalars(select(AdRecommendation)).one()
    assert client.post(f"/api/ad-recommendations/{rec.id}/dismiss").status_code == 200
    db_session.expire_all()
    assert rec.status == "dismissed"


def test_recommendation_not_found(client, db_session):
    setup_campaign(db_session)
    assert client.post("/api/ad-recommendations/99999/apply").status_code == 404
    assert client.post("/api/ad-recommendations/99999/dismiss").status_code == 404


def test_clock_uninitialized_returns_500(client, db_session):
    make_sp(db_session)  # 캠페인/시계 없이 매장만
    res = client.get("/api/ad-campaigns")
    assert res.status_code == 500
