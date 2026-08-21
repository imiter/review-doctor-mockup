from datetime import datetime, timezone

from app.llm import generate, onboarding
from app.models import GoldenExample, OnboardingScenario, Review


def test_find_uncovered_categories_excludes_covered_and_no_issue(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="옛날 리뷰", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    result = onboarding.find_uncovered_categories(db_session, sid)

    assert "hygiene" not in result
    assert "no_issue" not in result
    assert set(result) == {"food_quality", "delivery", "service", "price", "missing_or_wrong_item"}


def test_find_uncovered_categories_ignores_synthetic_examples(db_session, seeded_user):
    sid = seeded_user["store"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="범용 예시", reply_text="범용 답글",
        is_manual=False, is_synthetic=True, source="synthetic", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    result = onboarding.find_uncovered_categories(db_session, sid)

    assert "hygiene" in result  # synthetic 시드만으로는 커버된 것으로 치지 않는다


def test_find_uncovered_categories_all_six_for_fresh_store(db_session, seeded_user):
    result = onboarding.find_uncovered_categories(db_session, seeded_user["store"].id)
    assert set(result) == {
        "food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item",
    }


def test_get_or_create_scenario_creates_new_one(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: "가상 리뷰 본문")
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "마중물 초안")

    scenario = onboarding.get_or_create_scenario(db_session, store, "hygiene")

    assert scenario.category == "hygiene"
    assert scenario.virtual_review_text == "가상 리뷰 본문"
    assert scenario.draft_text == "마중물 초안"
    assert scenario.status == "pending"
    assert db_session.query(Review).count() == 0  # 가상 리뷰는 reviews 테이블에 저장되지 않는다


def test_get_or_create_scenario_reuses_existing_without_calling_llm_again(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    calls = []
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: calls.append(1) or "가상 리뷰")
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "초안")

    first = onboarding.get_or_create_scenario(db_session, store, "hygiene")
    second = onboarding.get_or_create_scenario(db_session, store, "hygiene")

    assert first.id == second.id
    assert len(calls) == 1


def test_get_or_create_scenario_reuses_skipped_scenario_without_regenerating(db_session, seeded_user, monkeypatch):
    store = seeded_user["store"]
    db_session.add(OnboardingScenario(
        store_id=store.id, category="hygiene", virtual_review_text="가상 리뷰", draft_text="초안",
        status="skipped", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    def _boom(*a, **kw):
        raise AssertionError("이미 시나리오가 있으면 LLM을 다시 호출하면 안 된다")
    monkeypatch.setattr(onboarding.client, "call_haiku", _boom)

    scenario = onboarding.get_or_create_scenario(db_session, store, "hygiene")
    assert scenario.status == "skipped"


def test_generate_virtual_review_uses_category_label(monkeypatch):
    captured = {}

    def _fake_call_haiku(system, user, **kw):
        captured["user"] = user
        return "가상 리뷰"

    monkeypatch.setattr(onboarding.client, "call_haiku", _fake_call_haiku)

    result = onboarding.generate_virtual_review("hygiene")

    assert result == "가상 리뷰"
    assert "위생" in captured["user"]
