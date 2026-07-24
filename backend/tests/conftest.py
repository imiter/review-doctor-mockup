import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (Base.metadata에 전체 테이블 등록)
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # Auto-refresh objects after flush to populate relationships
    @event.listens_for(session, "after_flush")
    def refresh_relationships(session, flush_context):
        for obj in session.identity_map.values():
            if hasattr(obj.__class__, "reply"):
                session.expire(obj, ["reply"])

    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
