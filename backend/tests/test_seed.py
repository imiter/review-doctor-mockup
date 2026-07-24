from sqlalchemy import func, select

from app.models import (
    AdCampaign, AdRankSnapshot, MockClock, Order, OrderDeduction,
    ReplyTemplate, Review, Settlement, StorePlatform,
)
from app.seed.run import BASE_NOW, seed_all


def test_seed_settlement_invariant(db_session):
    seed_all(db_session)
    for settlement in db_session.scalars(select(Settlement)).all():
        orders = settlement.orders
        gross = sum(o.item_amount + o.delivery_tip for o in orders)
        ded = db_session.scalar(
            select(func.coalesce(func.sum(OrderDeduction.amount), 0))
            .join(Order, OrderDeduction.order_id == Order.id)
            .where(Order.settlement_id == settlement.id)
        )
        assert settlement.total_gross == gross
        assert settlement.total_deductions == ded
        assert settlement.net_payout == gross - ded


def test_seed_volumes(db_session):
    seed_all(db_session)
    assert db_session.scalar(select(func.count(StorePlatform.id))) == 4
    assert db_session.scalar(select(func.count(Review.id))) == 40
    assert db_session.scalar(select(func.count(ReplyTemplate.id))) == 9
    assert db_session.scalar(select(func.count(AdCampaign.id))) == 2
    assert db_session.scalar(select(func.count(AdRankSnapshot.id))) == 60  # 캠페인당 30
    order_count = db_session.scalar(select(func.count(Order.id)))
    assert 300 <= order_count <= 500
    assert db_session.get(MockClock, 1).mock_now == BASE_NOW


def test_seed_rank_slide_exists(db_session):
    seed_all(db_session)
    campaign = db_session.scalars(select(AdCampaign).where(AdCampaign.target_rank == 3)).first()
    ranks = db_session.scalars(
        select(AdRankSnapshot.my_rank)
        .where(AdRankSnapshot.campaign_id == campaign.id)
        .order_by(AdRankSnapshot.snapshot_at)
    ).all()
    assert ranks[0] == 3 and max(ranks) == 7  # 3위→7위 밀림 구간
