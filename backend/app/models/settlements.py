from datetime import date, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    payout_date: Mapped[date]
    total_gross: Mapped[int]
    total_deductions: Mapped[int]
    net_payout: Mapped[int]
    status: Mapped[str] = mapped_column(String(20))  # scheduled | paid

    orders: Mapped[list["Order"]] = relationship(back_populates="settlement")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_platform_id: Mapped[int] = mapped_column(ForeignKey("store_platforms.id"))
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("settlements.id"))
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    ordered_at: Mapped[datetime]
    item_amount: Mapped[int]
    delivery_tip: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="completed")

    settlement: Mapped[Settlement | None] = relationship(back_populates="orders")
    deductions: Mapped[list["OrderDeduction"]] = relationship(back_populates="order")


class OrderDeduction(Base):
    __tablename__ = "order_deductions"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="deductions")
