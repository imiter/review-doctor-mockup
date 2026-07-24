from datetime import datetime

from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation
from tests.test_models_reviews import make_sp


def test_ad_domain_chain(db_session):
    sp = make_sp(db_session)
    campaign = AdCampaign(
        store_platform_id=sp.id, category="치킨", current_cpc=400, target_rank=3, status="active"
    )
    db_session.add(campaign)
    db_session.flush()

    snap = AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0),
        my_rank=5, competitor_est_cpc=650,
    )
    db_session.add(snap)
    db_session.flush()

    rec = AdRecommendation(
        campaign_id=campaign.id, snapshot_id=snap.id,
        action_type="raise_cpc", suggested_cpc=700, status="pending",
        created_at=datetime(2026, 7, 25, 9, 0),
    )
    db_session.add(rec)
    db_session.flush()

    hist = AdBidHistory(
        campaign_id=campaign.id, recommendation_id=rec.id,
        old_cpc=400, new_cpc=700, applied_at=datetime(2026, 7, 25, 9, 10),
    )
    db_session.add(hist)
    db_session.flush()

    assert hist.recommendation_id == rec.id
    assert rec.snapshot_id == snap.id
