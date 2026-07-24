from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(String(20))

    stores: Mapped[list["Store"]] = relationship(back_populates="owner")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(200))

    owner: Mapped[Owner] = relationship(back_populates="stores")
    store_platforms: Mapped[list["StorePlatform"]] = relationship(back_populates="store")


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    default_commission_rate: Mapped[float]


class StorePlatform(Base):
    __tablename__ = "store_platforms"
    __table_args__ = (UniqueConstraint("store_id", "platform_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    platform_store_name: Mapped[str] = mapped_column(String(100))

    store: Mapped[Store] = relationship(back_populates="store_platforms")
    platform: Mapped[Platform] = relationship()


class MockClock(Base):
    __tablename__ = "mock_clock"

    id: Mapped[int] = mapped_column(primary_key=True)
    mock_now: Mapped[datetime]
