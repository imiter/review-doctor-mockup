import pytest

from app.llm.embedding import cosine_similarity
from app.llm.feedback import record_draft_feedback_background
from app.models import DraftFeedbackScore


def test_cosine_similarity_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero_without_raising():
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_record_draft_feedback_background_writes_score_row(db_session, seeded_user, monkeypatch):
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]
    monkeypatch.setattr(feedback_mod, "embed_documents", lambda texts: [[1.0, 0.0], [1.0, 0.0]])
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)
    created_feedback = []

    class _FakeClient:
        def create_feedback(self, **kwargs):
            created_feedback.append(kwargs)

    monkeypatch.setattr(feedback_mod, "_LangSmithClient", _FakeClient)

    record_draft_feedback_background(
        trace_id="44444444-4444-4444-4444-444444444444",
        draft_text="초안입니다", final_text="최종본입니다",
        store_id=store.id, category="food_quality",
        source_review_id=None, source_scenario_id=None,
    )

    row = db_session.query(DraftFeedbackScore).one()
    assert row.category == "food_quality"
    assert float(row.similarity_score) == pytest.approx(1.0)
    assert created_feedback == [{
        "run_id": "44444444-4444-4444-4444-444444444444",
        "key": "draft_final_similarity", "score": pytest.approx(1.0),
    }]


def test_record_draft_feedback_background_skips_silently_when_embedding_fails(db_session, seeded_user, monkeypatch):
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]

    def _boom(texts):
        raise RuntimeError("Voyage 호출 실패")

    monkeypatch.setattr(feedback_mod, "embed_documents", _boom)
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)

    record_draft_feedback_background(
        trace_id="55555555-5555-5555-5555-555555555555",
        draft_text="초안", final_text="최종본",
        store_id=store.id, category="delivery",
    )

    assert db_session.query(DraftFeedbackScore).count() == 0


def test_record_draft_feedback_background_keeps_db_row_when_langsmith_fails(db_session, seeded_user, monkeypatch):
    """DB 기록은 LangSmith 기록과 독립적이어야 한다 — LangSmith가 실패해도
    이미 계산된 유사도 점수는 DB에 남아야 한다(대시보드가 이 DB만 읽는다)."""
    import app.llm.feedback as feedback_mod

    store = seeded_user["store"]
    monkeypatch.setattr(feedback_mod, "embed_documents", lambda texts: [[1.0, 0.0], [0.0, 1.0]])
    monkeypatch.setattr(feedback_mod, "SessionLocal", lambda: db_session)

    class _FailingClient:
        def create_feedback(self, **kwargs):
            raise RuntimeError("LangSmith API 실패")

    monkeypatch.setattr(feedback_mod, "_LangSmithClient", _FailingClient)

    record_draft_feedback_background(
        trace_id="66666666-6666-6666-6666-666666666666",
        draft_text="초안", final_text="완전히 다른 최종본",
        store_id=store.id, category="service",
    )

    row = db_session.query(DraftFeedbackScore).one()
    assert float(row.similarity_score) == pytest.approx(0.0)
