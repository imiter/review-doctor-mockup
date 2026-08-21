from app.models import GoldenExample
from scripts.seed_synthetic_golden_examples import _SEED_EXAMPLES, seed_synthetic_golden_examples

_ALL_CATEGORIES = {"food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item"}


def test_seed_creates_one_example_per_category_per_store(db_session, seeded_user):
    count = seed_synthetic_golden_examples(db_session)
    assert count == 6

    rows = db_session.query(GoldenExample).filter_by(
        store_id=seeded_user["store"].id, is_synthetic=True,
    ).all()
    assert {r.category for r in rows} == _ALL_CATEGORIES
    assert all(r.is_manual is False for r in rows)
    assert all(r.source == "synthetic" for r in rows)
    assert all(r.review_text and r.reply_text for r in rows)


def test_seed_is_idempotent(db_session, seeded_user):
    seed_synthetic_golden_examples(db_session)
    second_count = seed_synthetic_golden_examples(db_session)
    assert second_count == 0
    assert db_session.query(GoldenExample).filter_by(
        store_id=seeded_user["store"].id, is_synthetic=True,
    ).count() == 6


def test_seed_covers_all_valid_categories():
    assert set(_SEED_EXAMPLES.keys()) == _ALL_CATEGORIES
