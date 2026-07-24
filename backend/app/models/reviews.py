from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    rating: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    reviewer_name: Mapped[str] = mapped_column(String(50))
    has_photo: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="unanswered")
    created_at: Mapped[datetime]

    store_platform: Mapped["StorePlatform"] = relationship()
    reply: Mapped["ReviewReply | None"] = relationship(back_populates="review")


class ReplyStyle(Base):
    __tablename__ = "reply_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200))


class ReplyTemplate(Base):
    __tablename__ = "reply_templates"
    __table_args__ = (UniqueConstraint("style_id", "rating_band"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    style_id: Mapped[int] = mapped_column(ForeignKey("reply_styles.id"))
    rating_band: Mapped[str] = mapped_column(String(10))  # low | mid | high
    template_text: Mapped[str] = mapped_column(Text)


class ReviewReply(Base):
    __tablename__ = "review_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), unique=True)
    style_id: Mapped[int] = mapped_column(ForeignKey("reply_styles.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]

    review: Mapped[Review] = relationship(back_populates="reply")


from app.models.core import StorePlatform  # noqa: E402  (관계 타입 해석용)
