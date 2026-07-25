from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  Base.metadata에 전체 테이블 등록
from app.auth import hash_password
from app.db import Base, get_db
from app.main import app
from app.models import Platform, ReplyStyle, Store, StorePlatformConnection, Subscription, User


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def platforms(db_session):
    rows = {
        "baemin": Platform(code="baemin", name="배달의민족", brand_color="#2AC1BC", default_commission_rate="0.068"),
        "coupang_eats": Platform(code="coupang_eats", name="쿠팡이츠", brand_color="#F7552F", default_commission_rate="0.098"),
        "yogiyo": Platform(code="yogiyo", name="요기요", brand_color="#FA0050", default_commission_rate="0.125"),
    }
    db_session.add_all(rows.values())
    db_session.flush()
    return rows


@pytest.fixture()
def reply_styles(db_session):
    style = ReplyStyle(
        name="발랄 이모지 파티", description="테스트용",
        template_high="{nickname}님 {menu} 최고예요! {store} 감사합니다.",
        template_mid="{nickname}님 {menu} 아쉬웠어요. {store} 개선할게요.",
        template_low="{nickname}님 {menu} 죄송합니다. {store} 바로 고칠게요.",
    )
    db_session.add(style)
    db_session.flush()
    return style


@pytest.fixture()
def seeded_user(db_session, platforms):
    user = User(
        email="demo@dris.kr", password_hash=hash_password("demo1234!"), nickname="김사장",
        phone_hash="a" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()

    store = Store(user_id=user.id, name="치킨대장", category="치킨", created_at=datetime.now(timezone.utc))
    db_session.add(store)
    db_session.flush()

    db_session.add(StorePlatformConnection(
        store_id=store.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-1", business_number="000-00-00001",
        connected_at=datetime.now(timezone.utc),
    ))
    db_session.add(Subscription(user_id=user.id, plan="basic", daily_reply_limit=10, started_at=date.today()))
    db_session.commit()
    return {"user": user, "store": store}


@pytest.fixture()
def auth_headers(client, seeded_user):
    res = client.post("/auth/login", json={"email": "demo@dris.kr", "password": "demo1234!"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
