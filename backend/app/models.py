"""SQLAlchemy 모델 — schema.sql의 18개 테이블과 1:1 대응.

스키마 정본은 저장소 루트 schema.sql이다. 여기서는 컬럼과 관계만 미러링하며,
제약(CHECK, ON DELETE 정책)은 DB 레벨에 이미 존재한다.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    marketing_agreed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]

    stores: Mapped[list["Store"]] = relationship(back_populates="user")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="user")
    social_accounts: Mapped[list["SocialAccount"]] = relationship(back_populates="user")


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_user_id: Mapped[str] = mapped_column(String(100))
    connected_at: Mapped[datetime]

    user: Mapped[User] = relationship(back_populates="social_accounts")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime]

    user: Mapped[User] = relationship(back_populates="stores")


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    brand_color: Mapped[str | None] = mapped_column(String(7))
    default_commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))


class StorePlatformConnection(Base):
    __tablename__ = "store_platform_connections"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    platform_store_id: Mapped[str] = mapped_column(String(30))
    business_number: Mapped[str | None] = mapped_column(String(20))
    connected_at: Mapped[datetime]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(10), default="basic")
    daily_reply_limit: Mapped[int] = mapped_column(default=10)
    started_at: Mapped[date]
    expires_at: Mapped[date | None]

    user: Mapped[User] = relationship(back_populates="subscription")


class ReplyStyle(Base):
    __tablename__ = "reply_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    template_high: Mapped[str] = mapped_column(Text)
    template_mid: Mapped[str] = mapped_column(Text)
    template_low: Mapped[str] = mapped_column(Text)


class ReplySetting(Base):
    __tablename__ = "reply_settings"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), unique=True)
    style_id: Mapped[int] = mapped_column(ForeignKey("reply_styles.id"))
    promo_text: Mapped[str | None] = mapped_column(String(400))
    include_nickname: Mapped[bool] = mapped_column(default=True)
    include_menu: Mapped[bool] = mapped_column(default=True)
    include_store_name: Mapped[bool] = mapped_column(default=True)
    promo_on_negative: Mapped[bool] = mapped_column(default=False)
    auto_reply_enabled: Mapped[bool] = mapped_column(default=False)
    auto_reply_min_rating: Mapped[int] = mapped_column(default=1)

    style: Mapped[ReplyStyle] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    order_no: Mapped[str] = mapped_column(String(30), unique=True)
    ordered_at: Mapped[datetime]
    menu_summary: Mapped[str] = mapped_column(String(200))
    order_type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()
    review: Mapped["Review | None"] = relationship(back_populates="order")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), unique=True)
    rating: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    customer_nickname: Mapped[str] = mapped_column(String(50))
    customer_order_count: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(12), default="unanswered")
    created_at: Mapped[datetime]

    order: Mapped[Order] = relationship(back_populates="review")
    replies: Mapped[list["ReviewReply"]] = relationship(back_populates="review")


class ReviewReply(Base):
    __tablename__ = "review_replies"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    review_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    reply_type: Mapped[str] = mapped_column(String(10))
    style_id: Mapped[int | None] = mapped_column(ForeignKey("reply_styles.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]

    review: Mapped[Review] = relationship(back_populates="replies")


class DailySettlement(Base):
    __tablename__ = "daily_settlements"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    settle_date: Mapped[date]
    sales_amount: Mapped[int] = mapped_column(default=0)
    deposit_amount: Mapped[int] = mapped_column(default=0)

    platform: Mapped[Platform] = relationship()


class RepurchaseMetric(Base):
    __tablename__ = "repurchase_metrics"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    metric_date: Mapped[date]
    new_orders: Mapped[int] = mapped_column(default=0)
    repeat_orders: Mapped[int] = mapped_column(default=0)
    rate_raw: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    rate_adjusted: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(30))
    current_cpc: Mapped[int]
    target_rank: Mapped[int]
    status: Mapped[str] = mapped_column(String(10), default="active")

    store: Mapped[Store] = relationship()


class AdPerformanceMetric(Base):
    __tablename__ = "ad_performance_metrics"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ad_campaigns.id"))
    metric_date: Mapped[date]
    ad_spend: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    ad_orders: Mapped[int] = mapped_column(default=0)
    ad_revenue: Mapped[int] = mapped_column(default=0)


class AdRankSnapshot(Base):
    __tablename__ = "ad_rank_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ad_campaigns.id"))
    snapshot_at: Mapped[datetime]
    current_rank: Mapped[int]
    competitor_est_cpc: Mapped[int | None]
    status: Mapped[str | None] = mapped_column(String(12))
    recommended_action: Mapped[str] = mapped_column(String(10), default="keep")
    suggested_cpc: Mapped[int | None]
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    point_label: Mapped[str | None] = mapped_column(String(20))
    total_scanned: Mapped[int | None]
    ads_above: Mapped[int | None]


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    alert_type: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(String(300))
    is_read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]


class SignupVerification(Base):
    __tablename__ = "signup_verifications"
    __table_args__ = (UniqueConstraint("target", "purpose"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    target: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]
