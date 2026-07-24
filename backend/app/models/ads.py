from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    category: Mapped[str] = mapped_column(String(30))
    current_cpc: Mapped[int]
    target_rank: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused

    store_platform: Mapped["StorePlatform"] = relationship()


class AdRankSnapshot(Base):
    __tablename__ = "ad_rank_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    snapshot_at: Mapped[datetime]
    my_rank: Mapped[int]
    competitor_est_cpc: Mapped[int]


class AdRecommendation(Base):
    __tablename__ = "ad_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ad_rank_snapshots.id"))
    action_type: Mapped[str] = mapped_column(String(20))  # raise_cpc | keep | lower_cpc
    suggested_cpc: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime]


class AdBidHistory(Base):
    __tablename__ = "ad_bid_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("ad_recommendations.id"))
    old_cpc: Mapped[int]
    new_cpc: Mapped[int]
    applied_at: Mapped[datetime]


from app.models.core import StorePlatform  # noqa: E402
