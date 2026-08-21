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


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch):
    """테스트 스위트가 실제 Anthropic API를 호출하지 않도록 매 테스트마다
    ANTHROPIC_API_KEY를 제거한다. classify_review/generate_ai_reply를 직접
    monkeypatch하지 않는 테스트(예: review_sync의 분류 실패 폴백 테스트)는
    키가 우연히 설정된 환경(실 배포 프로세스와 같은 환경변수를 공유하는
    로컬/CI)에서 실제 과금되는 API를 호출할 수 있기 때문 — 이 fixture는
    autouse라 테스트 본문보다 먼저 실행되지만, 테스트가 자체적으로
    monkeypatch.setenv("ANTHROPIC_API_KEY", ...)를 호출하면 그 테스트
    안에서는 그 값이 그대로 우선한다."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # autoflush=False matches app.db.SessionLocal (the session review_sync.py's
    # background job actually runs under in production) — with the default
    # autoflush=True, a select() silently sees a prior add()'s pending row
    # even before it's flushed, masking bugs that only reproduce when the
    # session doesn't auto-flush (e.g. two upserts for the same key without
    # a flush between them).
    session = sessionmaker(bind=engine, autoflush=False)()
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


@pytest.fixture()
def signup_flow(client, monkeypatch):
    """이메일 인증 코드를 고정값("123456")으로 monkeypatch해서 /auth/signup까지 한 번에
    통과시켜주는 헬퍼. 실제 이메일 발송은 no-op으로 막는다."""
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "generate_code", lambda: "123456")
    monkeypatch.setattr(auth_router, "send_verification_email", lambda to, code: None)

    def _run(email: str, **overrides):
        email_res = client.post("/auth/signup/email-code", json={"email": email})
        assert email_res.status_code == 200, email_res.text
        payload = {
            "email": email, "email_code": "123456",
            "password": "pw12345!", "nickname": "테스트", "marketing_agreed": False,
        }
        payload.update(overrides)
        return client.post("/auth/signup", json=payload)

    return _run
