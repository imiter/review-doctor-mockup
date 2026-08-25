"""golden_examples.embedding은 pgvector `vector(1024)` 컬럼이고, 실제 순위
계산(ORDER BY embedding <-> :query)은 SQLite로는 검증할 수 없다(vector 타입
자체가 없음, app/llm/rag.py 모듈 docstring 참고). 이 파일만 로컬 Postgres
(pgvector 설치됨, docker의 baemin-verify-db2 컨테이너)에 대고 실제 SQL
실행으로 검증한다 — 나머지 스위트 전체가 쓰는 in-memory SQLite와는 별도
데이터베이스(delivery_insight_test)를 써서 실제 개발 DB 데이터를 건드리지
않는다. 로컬에 이 Postgres가 없으면(예: CI, 다른 개발자 환경) 이 파일
전체를 스킵한다."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.llm.rag import fetch_golden_examples
from app.models import GoldenExample, Store, User

_ADMIN_URL = "postgresql+psycopg://postgres:postgres@localhost:15432/postgres"
_TEST_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:15432/delivery_insight_test"


def _pg_available() -> bool:
    try:
        engine = create_engine(_ADMIN_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="로컬 Postgres(15432)에 연결할 수 없어 pgvector 테스트를 건너뜀")


@pytest.fixture(scope="module")
def pg_engine():
    admin_engine = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'delivery_insight_test'")).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE delivery_insight_test"))
    admin_engine.dispose()

    engine = create_engine(_TEST_DB_URL)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_db(pg_engine):
    connection = pg_engine.connect()
    trans = connection.begin()
    session = sessionmaker(bind=connection)()
    yield session
    session.close()
    trans.rollback()
    connection.close()


@pytest.fixture()
def pg_store(pg_db):
    user = User(nickname="테스트사장님", marketing_agreed=False, created_at=datetime.now(timezone.utc))
    pg_db.add(user)
    pg_db.flush()
    store = Store(user_id=user.id, name="테스트매장", category="치킨", created_at=datetime.now(timezone.utc))
    pg_db.add(store)
    pg_db.flush()
    return store


def _make_example(pg_db, store_id, *, category="food_quality", review_text, embedding, created_at, is_manual=True, is_synthetic=False):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text=review_text, reply_text="답글",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        embedding=embedding, created_at=created_at,
    )
    pg_db.add(ex)
    pg_db.flush()
    return ex


def test_ranks_by_cosine_distance_over_recency(pg_db, pg_store, monkeypatch):
    """의미적으로 더 가까운 예시가 최신순보다 우선해야 한다 — 카테고리당
    예시가 몇 개 없어 매번 같은 것만 반복 주입되던 문제(2026-08-26)를
    이 랭킹으로 해결한다."""
    now = datetime.now(timezone.utc)
    older_but_closer = _make_example(
        pg_db, pg_store.id, review_text="양이 너무 적어요",
        embedding=[1.0, 0.0] + [0.0] * 1022, created_at=now - timedelta(days=30),
    )
    newer_but_farther = _make_example(
        pg_db, pg_store.id, review_text="배달이 늦었어요",
        embedding=[0.0, 1.0] + [0.0] * 1022, created_at=now,
    )

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0] + [0.0] * 1022)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "양이 적었어요", limit=2)

    assert [r.id for r in result] == [older_but_closer.id, newer_but_farther.id]


def test_embedded_rows_ranked_before_unembedded(pg_db, pg_store, monkeypatch):
    now = datetime.now(timezone.utc)
    unembedded_but_newer = _make_example(pg_db, pg_store.id, review_text="리뷰1", embedding=None, created_at=now)
    embedded_but_older = _make_example(
        pg_db, pg_store.id, review_text="리뷰2",
        embedding=[1.0, 0.0] + [0.0] * 1022, created_at=now - timedelta(days=30),
    )

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0] + [0.0] * 1022)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "쿼리", limit=2)

    assert result[0].id == embedded_but_older.id  # 임베딩 있는 쪽이 먼저
    assert result[1].id == unembedded_but_newer.id


def test_falls_back_to_recency_when_embed_query_fails(pg_db, pg_store, monkeypatch):
    now = datetime.now(timezone.utc)
    older = _make_example(pg_db, pg_store.id, review_text="리뷰1", embedding=[1.0] + [0.0] * 1023, created_at=now - timedelta(days=5))
    newer = _make_example(pg_db, pg_store.id, review_text="리뷰2", embedding=[0.0] + [0.0] * 1023, created_at=now)

    import app.llm.rag as rag_mod

    def _raise(text):
        raise RuntimeError("Voyage API 장애")

    monkeypatch.setattr(rag_mod, "embed_query", _raise)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "쿼리", limit=2)

    assert [r.id for r in result] == [newer.id, older.id]
