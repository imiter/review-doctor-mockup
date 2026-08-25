from datetime import datetime, timezone

from app.models import GoldenExample
from scripts.backfill_golden_example_embeddings import backfill_golden_example_embeddings


def _make_example(db_session, store_id, *, review_text, embedding=None):
    ex = GoldenExample(
        store_id=store_id, category="food_quality",
        review_text=review_text, reply_text="답글",
        is_manual=True, is_synthetic=False, source="backfill",
        embedding=embedding, created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    return ex


def test_backfill_fills_embedding_for_rows_missing_it(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_embeddings as backfill_mod

    sid = seeded_user["store"].id
    ex = _make_example(db_session, sid, review_text="양이 너무 적어요")
    db_session.commit()

    monkeypatch.setattr(backfill_mod, "embed_documents", lambda texts: [[0.1, 0.2, 0.3]])

    result = backfill_golden_example_embeddings(db_session)

    assert result == {"updated": 1, "failed": 0, "skipped_empty": 0, "total": 1}
    db_session.refresh(ex)
    assert ex.embedding == [0.1, 0.2, 0.3]


def test_backfill_skips_rows_already_embedded(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_embeddings as backfill_mod

    sid = seeded_user["store"].id
    _make_example(db_session, sid, review_text="이미 임베딩 있음", embedding=[9.0])
    db_session.commit()

    monkeypatch.setattr(
        backfill_mod, "embed_documents",
        lambda texts: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )

    result = backfill_golden_example_embeddings(db_session)

    assert result == {"updated": 0, "failed": 0, "skipped_empty": 0, "total": 0}


def test_backfill_skips_rows_with_empty_review_text(db_session, seeded_user, monkeypatch):
    """빈 문자열이 배치에 섞이면 Voyage가 요청 전체를 400으로 거부한다
    (실측 확인, 2026-08-26) — 벡터화할 의미 있는 내용도 없으므로 아예
    건너뛰고 embedding은 NULL로 남긴다."""
    from scripts import backfill_golden_example_embeddings as backfill_mod

    sid = seeded_user["store"].id
    empty = _make_example(db_session, sid, review_text="")
    real = _make_example(db_session, sid, review_text="진짜 내용")
    db_session.commit()

    captured = {}

    def _fake_embed_documents(texts):
        captured["texts"] = texts
        return [[1.0] for _ in texts]

    monkeypatch.setattr(backfill_mod, "embed_documents", _fake_embed_documents)

    result = backfill_golden_example_embeddings(db_session)

    assert captured["texts"] == ["진짜 내용"]  # 빈 문자열은 배치에서 제외됨
    assert result == {"updated": 1, "failed": 0, "skipped_empty": 1, "total": 2}
    db_session.refresh(empty)
    db_session.refresh(real)
    assert empty.embedding is None
    assert real.embedding == [1.0]


def test_backfill_records_failed_batch_without_raising(db_session, seeded_user, monkeypatch):
    from scripts import backfill_golden_example_embeddings as backfill_mod

    sid = seeded_user["store"].id
    _make_example(db_session, sid, review_text="리뷰")
    db_session.commit()

    def _raise(texts):
        raise RuntimeError("Voyage API 장애")

    monkeypatch.setattr(backfill_mod, "embed_documents", _raise)

    result = backfill_golden_example_embeddings(db_session)

    assert result["updated"] == 0
    assert result["failed"] == 1
