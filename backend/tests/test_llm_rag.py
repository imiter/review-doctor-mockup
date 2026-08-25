from datetime import datetime, timedelta, timezone

import app.llm.rag as rag_mod
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, Review


def _make_example(db_session, store_id, *, category, is_manual, is_synthetic, created_at, review_text="리뷰", embedding=None):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text=review_text, reply_text="답글",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        embedding=embedding, created_at=created_at,
    )
    db_session.add(ex)
    return ex


def test_fetch_golden_examples_prefers_real_over_synthetic(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    real = _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", "쿼리 리뷰", limit=3)

    assert len(result) == 2
    assert result[0].id == real.id  # 진짜 예시가 먼저 나온다 ("prefer real over synthetic" 증명)


def test_fetch_golden_examples_backfills_with_synthetic_when_real_insufficient(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="hygiene", is_manual=False, is_synthetic=True, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", "쿼리 리뷰", limit=2)

    assert len(result) == 2
    assert result[0].is_manual is True
    assert result[1].is_synthetic is True


def test_fetch_golden_examples_filters_by_category(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", is_manual=True, is_synthetic=False, created_at=now)
    _make_example(db_session, sid, category="delivery", is_manual=True, is_synthetic=False, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리 리뷰", limit=3)

    assert len(result) == 1
    assert result[0].category == "delivery"


def test_fetch_golden_examples_ranks_by_semantic_similarity(db_session, seeded_user, monkeypatch):
    """의미적으로 더 가까운 예시가 최신순보다 우선해야 한다 — 카테고리당
    예시가 몇 개 없어 매번 같은 것만 반복 주입되던 문제(2026-08-26)를
    이 랭킹으로 해결한다."""
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    older_but_closer = _make_example(
        db_session, sid, category="food_quality", is_manual=True, is_synthetic=False,
        created_at=now - timedelta(days=30), review_text="양이 너무 적어요", embedding=[1.0, 0.0],
    )
    newer_but_farther = _make_example(
        db_session, sid, category="food_quality", is_manual=True, is_synthetic=False,
        created_at=now, review_text="배달이 늦었어요", embedding=[0.0, 1.0],
    )
    db_session.commit()

    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0])

    result = fetch_golden_examples(db_session, sid, "food_quality", "양이 적었어요", limit=2)

    assert result[0].id == older_but_closer.id
    assert result[1].id == newer_but_farther.id


def test_fetch_golden_examples_embedded_rows_ranked_before_unembedded(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    unembedded_but_newer = _make_example(
        db_session, sid, category="food_quality", is_manual=True, is_synthetic=False,
        created_at=now, embedding=None,
    )
    embedded_but_older = _make_example(
        db_session, sid, category="food_quality", is_manual=True, is_synthetic=False,
        created_at=now - timedelta(days=30), embedding=[1.0, 0.0],
    )
    db_session.commit()

    monkeypatch.setattr(rag_mod, "embed_query", lambda text: [1.0, 0.0])

    result = fetch_golden_examples(db_session, sid, "food_quality", "쿼리", limit=2)

    assert result[0].id == embedded_but_older.id  # 임베딩 있는 쪽이 먼저
    assert result[1].id == unembedded_but_newer.id


def test_fetch_golden_examples_falls_back_to_recency_when_embedding_unavailable(db_session, seeded_user):
    """VOYAGE_API_KEY가 없어 embed_query가 실패하면(이 fixture 스위트는
    _no_voyage_key로 항상 키를 지운다) 기존처럼 최신순으로 폴백해야
    한다 — 임베딩 API 가용성이 답글 생성 자체를 막으면 안 된다."""
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    older = _make_example(db_session, sid, category="food_quality", is_manual=True, is_synthetic=False, created_at=now - timedelta(days=5))
    newer = _make_example(db_session, sid, category="food_quality", is_manual=True, is_synthetic=False, created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "food_quality", "쿼리", limit=2)

    assert result[0].id == newer.id
    assert result[1].id == older.id


def test_count_recent_same_category_within_window(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="배달 늦어요",
        customer_nickname="손님", category="delivery", created_at=now - timedelta(days=5),
    ))
    db_session.add(Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="또 배달 늦어요",
        customer_nickname="손님2", category="delivery", created_at=now - timedelta(days=40),  # 창 밖
    ))
    db_session.commit()

    count = count_recent_same_category(db_session, sid, "delivery", days=30)

    assert count == 1
