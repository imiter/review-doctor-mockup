"""reply_embedding 백필 스크립트 — review_text 백필
(test_backfill_golden_example_embeddings.py)과 같은 보장을 벡터화 대상만
reply_text로 바꿔 검증한다."""

from datetime import datetime, timezone

from app.models import GoldenExample
from scripts.backfill_golden_example_reply_embeddings import (
    backfill_golden_example_reply_embeddings,
)


def _make_example(db_session, store_id, *, reply_text, reply_embedding=None):
    ex = GoldenExample(
        store_id=store_id, category="food_quality",
        review_text="리뷰 본문", reply_text=reply_text,
        is_manual=True, is_synthetic=False, source="backfill",
        embedding=None, reply_embedding=reply_embedding,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    return ex


def test_backfill_fills_reply_embedding_for_rows_missing_it(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_reply_embeddings as backfill_mod

    sid = seeded_user["store"].id
    ex = _make_example(db_session, sid, reply_text="불편을 드려 죄송합니다")
    db_session.commit()

    captured = {}

    def _fake_embed_documents(texts):
        captured["texts"] = texts
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(backfill_mod, "embed_documents", _fake_embed_documents)

    result = backfill_golden_example_reply_embeddings(db_session)

    # review_text가 아니라 reply_text를 벡터화해야 한다 — 이 체크가 바로
    # 2026-10-06 최종 리뷰(C3)에서 고친 "무엇을 비교하는가"의 핵심이다.
    assert captured["texts"] == ["불편을 드려 죄송합니다"]
    assert result == {"updated": 1, "failed": 0, "skipped_empty": 0, "total": 1}
    db_session.refresh(ex)
    assert ex.reply_embedding == [0.1, 0.2, 0.3]
    assert ex.embedding is None  # review_text 쪽은 건드리지 않는다


def test_backfill_skips_rows_already_embedded(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_reply_embeddings as backfill_mod

    sid = seeded_user["store"].id
    _make_example(db_session, sid, reply_text="이미 있음", reply_embedding=[9.0])
    db_session.commit()

    monkeypatch.setattr(
        backfill_mod, "embed_documents",
        lambda texts: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )

    result = backfill_golden_example_reply_embeddings(db_session)

    assert result == {"updated": 0, "failed": 0, "skipped_empty": 0, "total": 0}


def test_backfill_skips_rows_with_empty_reply_text(db_session, seeded_user, monkeypatch):
    """빈 문자열이 배치에 섞이면 Voyage가 요청 전체를 400으로 거부한다
    (실측 확인, 2026-08-26)."""
    from scripts import backfill_golden_example_reply_embeddings as backfill_mod

    sid = seeded_user["store"].id
    empty = _make_example(db_session, sid, reply_text="")
    real = _make_example(db_session, sid, reply_text="진짜 답글")
    db_session.commit()

    captured = {}

    def _fake_embed_documents(texts):
        captured["texts"] = texts
        return [[1.0] for _ in texts]

    monkeypatch.setattr(backfill_mod, "embed_documents", _fake_embed_documents)

    result = backfill_golden_example_reply_embeddings(db_session)

    assert captured["texts"] == ["진짜 답글"]
    assert result == {"updated": 1, "failed": 0, "skipped_empty": 1, "total": 2}
    db_session.refresh(empty)
    db_session.refresh(real)
    assert empty.reply_embedding is None
    assert real.reply_embedding == [1.0]


def test_backfill_records_failed_batch_without_raising(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_reply_embeddings as backfill_mod

    sid = seeded_user["store"].id
    _make_example(db_session, sid, reply_text="답글")
    db_session.commit()

    def _raise(texts):
        raise RuntimeError("Voyage API 장애")

    monkeypatch.setattr(backfill_mod, "embed_documents", _raise)

    result = backfill_golden_example_reply_embeddings(db_session)

    assert result["updated"] == 0
    assert result["failed"] == 1
