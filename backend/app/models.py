"""SQLAlchemy 모델 — schema.sql의 21개 테이블과 1:1 대응.

스키마 정본은 저장소 루트 schema.sql이다. 여기서는 컬럼과 관계만 미러링하며,
제약(CHECK, ON DELETE 정책)은 DB 레벨에 이미 존재한다.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()


class BaeminShopBrand(Base):
    __tablename__ = "baemin_shop_brands"
    __table_args__ = (UniqueConstraint("connection_id", "shop_no"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("store_platform_connections.id"))
    shop_no: Mapped[str] = mapped_column(String(20))
    shop_name: Mapped[str] = mapped_column(String(200))

    connection: Mapped["StorePlatformConnection"] = relationship()


class BrandMenuInfo(Base):
    __tablename__ = "brand_menu_info"
    __table_args__ = (UniqueConstraint("connection_id", "shop_no"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("store_platform_connections.id"))
    shop_no: Mapped[str] = mapped_column(String(20))
    store_intro: Mapped[str | None] = mapped_column(Text)
    food_origin: Mapped[str | None] = mapped_column(Text)
    menu_intro: Mapped[str | None] = mapped_column(Text)
    menu_items: Mapped[list[dict]] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=list)
    updated_at: Mapped[datetime]

    connection: Mapped["StorePlatformConnection"] = relationship()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(10), default="basic")
    daily_reply_limit: Mapped[int] = mapped_column(default=10)
    started_at: Mapped[date]
    expires_at: Mapped[date | None]

    user: Mapped[User] = relationship(back_populates="subscription")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    order_id: Mapped[str] = mapped_column(String(64), unique=True)
    plan: Mapped[str] = mapped_column(String(10), default="pro")
    amount: Mapped[int]
    status: Mapped[str] = mapped_column(String(10), default="pending")
    toss_payment_key: Mapped[str | None] = mapped_column(String(200))
    fail_reason: Mapped[str | None] = mapped_column(String(200))
    virtual_account_secret: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime]
    approved_at: Mapped[datetime | None]


class ReplyStyle(Base):
    __tablename__ = "reply_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    template_high: Mapped[str] = mapped_column(Text)
    template_mid: Mapped[str] = mapped_column(Text)
    template_low: Mapped[str] = mapped_column(Text)
    tone_instruction: Mapped[str] = mapped_column(Text)


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
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), unique=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    menu_summary: Mapped[str] = mapped_column(String(200))
    external_review_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), unique=True
    )
    platform_shop_no: Mapped[str | None] = mapped_column(String(20))
    rating: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    customer_nickname: Mapped[str] = mapped_column(String(50))
    customer_order_count: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(12), default="unanswered")
    category: Mapped[str] = mapped_column(String(24), default="no_issue")
    is_sensitive: Mapped[bool] = mapped_column(default=False)
    sentiment_conflict: Mapped[bool] = mapped_column(default=False)
    image_urls: Mapped[list[str]] = mapped_column(ARRAY(Text).with_variant(JSON(), "sqlite"), default=list)
    created_at: Mapped[datetime]

    order: Mapped[Order | None] = relationship(back_populates="review")
    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()
    replies: Mapped[list["ReviewReply"]] = relationship(back_populates="review")


class ReviewSyncJob(Base):
    __tablename__ = "review_sync_jobs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    triggered_by: Mapped[str] = mapped_column(String(10), default="manual")
    reviews_fetched: Mapped[int | None]
    reviews_inserted: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()


class ReviewReply(Base):
    __tablename__ = "review_replies"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    review_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    reply_type: Mapped[str] = mapped_column(String(10))
    style_id: Mapped[int | None] = mapped_column(ForeignKey("reply_styles.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]

    review: Mapped[Review] = relationship(back_populates="replies")


class GoldenExample(Base):
    __tablename__ = "golden_examples"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    review_text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text)
    is_manual: Mapped[bool]
    is_synthetic: Mapped[bool]
    source: Mapped[str] = mapped_column(String(16))
    source_review_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("reviews.id"))
    source_reply_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("review_replies.id"))
    # none_as_null=True — SQLite JSON 타입은 기본값(False)이면 Python None을
    # SQL NULL이 아니라 JSON 리터럴 "null"(텍스트)로 저장해, embedding.is_(None)
    # 같은 IS NULL 조회가 전혀 매칭되지 않는다(실측 확인, 2026-08-26).
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float).with_variant(JSON(none_as_null=True), "sqlite"))
    created_at: Mapped[datetime]


class StoreStyleProfile(Base):
    __tablename__ = "store_style_profile"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    rules: Mapped[str] = mapped_column(Text)
    generated_from_count: Mapped[int]
    updated_at: Mapped[datetime]


class DailySettlement(Base):
    __tablename__ = "daily_settlements"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    settle_date: Mapped[date]
    sales_amount: Mapped[int] = mapped_column(default=0)
    deposit_amount: Mapped[int] = mapped_column(default=0)
    commission_amount: Mapped[int | None] = mapped_column(default=None)
    delivery_fee_amount: Mapped[int | None] = mapped_column(default=None)
    customer_discount_amount: Mapped[int | None] = mapped_column(default=None)
    ad_cost_amount: Mapped[int | None] = mapped_column(default=None)

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
    shop_no: Mapped[str | None] = mapped_column(String(20), default=None)

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


class BrandAdClickMetric(Base):
    __tablename__ = "brand_ad_click_metrics"
    __table_args__ = (UniqueConstraint("store_id", "platform_id", "shop_no", "metric_date"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    shop_no: Mapped[str] = mapped_column(String(20))
    metric_date: Mapped[date]
    ad_spend: Mapped[int] = mapped_column(default=0)
    impressions: Mapped[int] = mapped_column(default=0)
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
    bid_at_snapshot: Mapped[int | None]


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


class OnboardingScenario(Base):
    __tablename__ = "onboarding_scenarios"
    __table_args__ = (UniqueConstraint("store_id", "category"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category: Mapped[str] = mapped_column(String(24))
    virtual_review_text: Mapped[str] = mapped_column(Text)
    draft_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    shown_on: Mapped[date | None]
    created_at: Mapped[datetime]

    store: Mapped[Store] = relationship()
