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
from app.models import GoldenExample, Platform, Review, Store, User

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
    # drop_all을 먼저 한다 — create_all은 "없는 테이블만" 만들기 때문에,
    # 지난 실행에서 만들어진 테이블이 그대로 남아 있으면 그 뒤에 추가된
    # 컬럼(예: 2026-10-06의 golden_examples.reply_embedding)이 영원히
    # 반영되지 않아 UndefinedColumn으로 깨진다(실측). 이 DB는 이 파일
    # 전용(delivery_insight_test)이라 매번 비우고 다시 만들어도 안전하다.
    Base.metadata.drop_all(engine)
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


def _make_example(pg_db, store_id, *, category="food_quality", review_text, embedding, created_at, is_manual=True, is_synthetic=False, source="backfill", source_review_id=None, reply_text="답글", reply_embedding=None):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text=review_text, reply_text=reply_text,
        is_manual=is_manual, is_synthetic=is_synthetic, source=source,
        source_review_id=source_review_id,
        embedding=embedding, reply_embedding=reply_embedding, created_at=created_at,
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


def test_check_voice_consistency_none_when_baseline_insufficient(pg_db, pg_store):
    from app.llm.rag import check_voice_consistency

    result = check_voice_consistency(pg_db, pg_store.id, "delivery", [0.1] * 1024)

    assert result is None


def test_check_voice_consistency_true_when_close_to_baseline(pg_db, pg_store):
    from app.llm.rag import check_voice_consistency

    base_vec = [0.5] * 1024
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"리뷰{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    result = check_voice_consistency(pg_db, pg_store.id, "delivery", base_vec)

    assert result is True


def test_check_voice_consistency_false_when_outlier(pg_db, pg_store):
    from app.llm.rag import check_voice_consistency

    base_vec = [1.0] + [0.0] * 1023
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"리뷰{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()
    outlier_vec = [0.0] * 1023 + [1.0]  # base_vec과 직교(코사인 거리 최대)

    result = check_voice_consistency(pg_db, pg_store.id, "delivery", outlier_vec)

    assert result is False


def test_check_voice_consistency_ignores_rows_without_reply_embedding(pg_db, pg_store):
    """reply_embedding이 없는 행(백필 전/Voyage 실패)은 베이스라인에서
    빠진다 — review_text 임베딩(embedding)만 있는 행을 세어 넘기면 거리
    계산에 쓸 값이 없는 행을 "기준이 충분하다"고 오판하게 된다."""
    from app.llm.rag import check_voice_consistency

    for i in range(5):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"리뷰{i}", embedding=[0.5] * 1024, reply_embedding=None,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    assert check_voice_consistency(pg_db, pg_store.id, "delivery", [0.5] * 1024) is None


def test_check_voice_consistency_compares_reply_text_not_review_text(pg_db, pg_store):
    """핵심 회귀 테스트(2026-10-06 최종 리뷰 C3) — 리뷰 내용 유사도와 답글
    말투 유사도가 **정반대 판정**을 내는 상황을 만들어, 함수가 답글 쪽을
    따르는지 확인한다.

    베이스라인 3건은 review_text 임베딩(embedding)은 후보와 완전히 같고
    (= 리뷰 내용은 판박이), 답글 임베딩(reply_embedding)은 후보와 직교한다
    (= 말투는 완전히 다름). 옛 구현(embedding 비교)은 거리 0 → True를
    냈지만, 올바른 구현은 거리 최대 → False여야 한다."""
    from app.llm.rag import check_voice_consistency

    candidate_vec = [1.0] + [0.0] * 1023
    orthogonal_vec = [0.0] * 1023 + [1.0]

    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"배달이 늦었어요{i}",
            embedding=candidate_vec,        # 리뷰 내용은 후보와 동일
            reply_embedding=orthogonal_vec,  # 답글 말투는 후보와 정반대
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    assert check_voice_consistency(pg_db, pg_store.id, "delivery", candidate_vec) is False

    # 대칭 확인: 반대로 뒤집으면(리뷰 내용은 정반대, 답글 말투는 동일)
    # 판정도 뒤집혀야 한다 — 함수가 정말 답글 쪽만 본다는 증거.
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="hygiene",
            review_text=f"위생 문제{i}",
            embedding=orthogonal_vec,       # 리뷰 내용은 후보와 정반대
            reply_embedding=candidate_vec,  # 답글 말투는 후보와 동일
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    assert check_voice_consistency(pg_db, pg_store.id, "hygiene", candidate_vec) is True


def test_check_voice_consistency_counts_onboarding_as_baseline(pg_db, pg_store):
    """온보딩(경로 B) 답변도 사장님이 직접 쓴 글이라 베이스라인에 들어간다
    (2026-10-06 최종 리뷰 I6). 빼놓으면 온보딩 데이터만 있는 신생 매장은
    베이스라인이 영원히 0이라 이 체크 자체가 무력화된다."""
    from app.llm.rag import check_voice_consistency

    base_vec = [1.0] + [0.0] * 1023
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery", source="onboarding",
            review_text=f"가상리뷰{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    # 판단 불가(None)가 아니라 실제 판정이 나와야 한다.
    assert check_voice_consistency(pg_db, pg_store.id, "delivery", base_vec) is True
    assert check_voice_consistency(pg_db, pg_store.id, "delivery", [0.0] * 1023 + [1.0]) is False


def test_check_voice_consistency_excludes_organic_direct_from_baseline(pg_db, pg_store):
    """경로 C(organic_direct)는 지금 검증 대상이라 기준에서 빼야 한다 —
    자기 자신을 기준으로 재면 체크가 의미를 잃는다."""
    from app.llm.rag import check_voice_consistency

    base_vec = [1.0] + [0.0] * 1023
    for i in range(5):
        _make_example(
            pg_db, pg_store.id, category="delivery", source="organic_direct",
            review_text=f"리뷰{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    assert check_voice_consistency(pg_db, pg_store.id, "delivery", base_vec) is None


def _make_review(pg_db, store_id, *, category, content):
    platform = Platform(code="baemin", name="배달의민족", brand_color="#2AC1BC", default_commission_rate="0.068")
    pg_db.add(platform)
    pg_db.flush()
    review = Review(
        store_id=store_id, platform_id=platform.id, menu_summary="후라이드치킨",
        rating=5, content=content, customer_nickname="단골손님",
        category=category, created_at=datetime.now(timezone.utc),
    )
    pg_db.add(review)
    pg_db.flush()
    return review


# 위 3개 테스트는 check_voice_consistency 자체의 반환값만 본다 — 아래 3개는
# 리뷰 코드 수정 시 받은 지적(promote_direct_reply_to_golden_example가 그
# 결과를 바탕으로 실제 GoldenExample.needs_confirmation에 쓰는 한 줄,
# `needs_confirmation = consistent is False`, 이 한 줄에 대해서는 아무
# 테스트도 없었다)을 메운다. `not consistent`나 `consistent != True`로
# "단순화"되면 baseline 부족(None, 판단 불가)이 조용히 True(확인 필요)로
# 뒤집히는데, 위 3개 테스트는 이 함수를 호출하지 않아 그 회귀를 못 잡는다.
def test_promote_direct_reply_sets_needs_confirmation_true_when_outlier(pg_db, pg_store, monkeypatch):
    from app.llm.rag import promote_direct_reply_to_golden_example

    base_vec = [1.0] + [0.0] * 1023
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"기준{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    outlier_vec = [0.0] * 1023 + [1.0]  # 기준 벡터와 직교(코사인 거리 최대)
    review = _make_review(pg_db, pg_store.id, category="delivery", content="배달이 너무 늦었어요")

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "compute_golden_example_embedding", lambda text: outlier_vec)

    example = promote_direct_reply_to_golden_example(pg_db, review, reply_id=None, reply_text="답글입니다")
    pg_db.commit()

    assert example.needs_confirmation is True
    persisted = pg_db.get(GoldenExample, example.id)
    assert persisted.needs_confirmation is True
    # 승격된 행에도 reply_embedding이 저장돼야 다음 후보의 기준이 될 수 있다.
    assert persisted.reply_embedding is not None


def test_promote_direct_reply_sets_needs_confirmation_false_when_consistent(pg_db, pg_store, monkeypatch):
    from app.llm.rag import promote_direct_reply_to_golden_example

    base_vec = [0.5] * 1024
    for i in range(3):
        _make_example(
            pg_db, pg_store.id, category="delivery",
            review_text=f"기준{i}", embedding=None, reply_embedding=base_vec,
            created_at=datetime.now(timezone.utc),
        )
    pg_db.commit()

    review = _make_review(pg_db, pg_store.id, category="delivery", content="배달이 빨랐어요")

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "compute_golden_example_embedding", lambda text: base_vec)

    example = promote_direct_reply_to_golden_example(pg_db, review, reply_id=None, reply_text="답글입니다")
    pg_db.commit()

    assert example.needs_confirmation is False
    persisted = pg_db.get(GoldenExample, example.id)
    assert persisted.needs_confirmation is False


def test_promote_direct_reply_sets_needs_confirmation_false_when_baseline_insufficient(pg_db, pg_store, monkeypatch):
    """베이스라인(organic/backfill/onboarding, reply_embedding 있는 것)이 3개 미만이면
    check_voice_consistency는 None(판단 불가)을 반환한다 — 이때
    needs_confirmation은 True가 아니라 False여야 한다. `not consistent`나
    `consistent != True`로 구현했다면 None도 True로 취급돼 이 테스트가
    깨진다(이 회귀가 바로 리뷰에서 지적한 지점)."""
    from app.llm.rag import promote_direct_reply_to_golden_example

    # 베이스라인 0개 — 아무 골든 예시도 미리 심지 않는다.
    review = _make_review(pg_db, pg_store.id, category="delivery", content="배달이 보통이었어요")

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "compute_golden_example_embedding", lambda text: [0.1] * 1024)

    example = promote_direct_reply_to_golden_example(pg_db, review, reply_id=None, reply_text="답글입니다")
    pg_db.commit()

    assert example.needs_confirmation is False
    persisted = pg_db.get(GoldenExample, example.id)
    assert persisted.needs_confirmation is False


# ---------------------------------------------------------------------------
# source 기반 3단계 우선순위(2026-10-06)와 코사인 거리 순위가 함께 걸려
# 있으므로, "단계가 유사도를 이긴다"는 핵심 보장은 실제 pgvector 순위 계산
# 위에서만 증명된다 — SQLite 스위트(tests/test_llm_rag.py)는 임베딩이 없어
# 최신순 폴백 경로로만 단계를 검증한다.
# ---------------------------------------------------------------------------
def _platform(pg_db):
    platform = Platform(code="baemin", name="배달의민족", brand_color="#2AC1BC", default_commission_rate="0.068")
    pg_db.add(platform)
    pg_db.flush()
    return platform


def _review(pg_db, store_id, platform_id, *, category="no_issue", sentiment_conflict=False, created_at):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="후라이드치킨",
        rating=5, content="리뷰 본문", customer_nickname="손님",
        category=category, sentiment_conflict=sentiment_conflict, created_at=created_at,
    )
    pg_db.add(review)
    pg_db.flush()
    return review


def test_tier_priority_beats_cosine_distance(pg_db, pg_store, monkeypatch):
    """쿼리와 완전히 직교하는(가장 먼) 1단계 예시가, 쿼리와 정확히 일치하는
    2단계 예시보다 먼저 나와야 한다 — 유사도는 단계 안에서만 순위를 매긴다."""
    now = datetime.now(timezone.utc)
    platform = _platform(pg_db)
    for _ in range(5):  # 신호 (a)(그 리뷰 시점 누적 ≤3) 끄기
        _review(pg_db, pg_store.id, platform.id, created_at=now - timedelta(days=10))
    signal_review = _review(pg_db, pg_store.id, platform.id, category="delivery", created_at=now)

    far_but_tier1 = _make_example(
        pg_db, pg_store.id, review_text="1단계", embedding=[0.0, 1.0] + [0.0] * 1022,
        created_at=now - timedelta(days=30), source="organic", source_review_id=signal_review.id,
    )
    exact_but_tier2 = _make_example(
        pg_db, pg_store.id, review_text="2단계", embedding=[1.0, 0.0] + [0.0] * 1022,
        created_at=now, source="organic",
    )

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0] + [0.0] * 1022)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "쿼리", limit=2)

    assert [r.id for r in result] == [far_but_tier1.id, exact_but_tier2.id]


def test_onboarding_tier_is_last_even_when_closest(pg_db, pg_store, monkeypatch):
    now = datetime.now(timezone.utc)
    exact_but_onboarding = _make_example(
        pg_db, pg_store.id, review_text="온보딩", embedding=[1.0, 0.0] + [0.0] * 1022,
        created_at=now, source="onboarding",
    )
    far_but_organic = _make_example(
        pg_db, pg_store.id, review_text="organic", embedding=[0.0, 1.0] + [0.0] * 1022,
        created_at=now - timedelta(days=30), source="organic",
    )

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0] + [0.0] * 1022)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "쿼리", limit=2)

    assert [r.id for r in result] == [far_but_organic.id, exact_but_onboarding.id]


def test_ranks_by_cosine_distance_within_tier1(pg_db, pg_store, monkeypatch):
    """단계 안에서는 기존과 똑같이 유사도 순위다 — 1단계로 범위가 좁혀져도
    최신순으로 되돌아가지 않는다."""
    now = datetime.now(timezone.utc)
    platform = _platform(pg_db)
    signal_a = _review(pg_db, pg_store.id, platform.id, category="delivery", created_at=now)
    signal_b = _review(pg_db, pg_store.id, platform.id, category="delivery", created_at=now)

    older_but_closer = _make_example(
        pg_db, pg_store.id, review_text="양이 너무 적어요", embedding=[1.0, 0.0] + [0.0] * 1022,
        created_at=now - timedelta(days=30), source="organic", source_review_id=signal_a.id,
    )
    newer_but_farther = _make_example(
        pg_db, pg_store.id, review_text="배달이 늦었어요", embedding=[0.0, 1.0] + [0.0] * 1022,
        created_at=now, source="organic", source_review_id=signal_b.id,
    )

    import app.llm.rag as rag_mod
    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0] + [0.0] * 1022)

    result = fetch_golden_examples(pg_db, pg_store.id, "food_quality", "쿼리", limit=2)

    assert [r.id for r in result] == [older_but_closer.id, newer_but_farther.id]
