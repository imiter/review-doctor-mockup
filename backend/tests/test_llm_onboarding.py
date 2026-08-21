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


def test_get_or_create_scenario_recovers_from_concurrent_insert_race(db_session, seeded_user, monkeypatch):
    """check-then-insert에는 락이 없어서, 두 요청이 거의 동시에 같은
    (store_id, category)로 들어오면 둘 다 처음의 SELECT를 통과한 뒤 하나만
    커밋에 성공하고 나머지는 UNIQUE (store_id, category) 위반으로 커밋이
    실패할 수 있다(온보딩 라우터의 실서비스 시나리오: 대시보드 카드가 두 번
    마운트되거나 요청이 재시도되는 경우). 스레드 없이 단일 프로세스 안에서
    같은 레이스를 재현하려고, LLM 호출(call_haiku) 안에서 "다른 요청이 먼저
    커밋한 것"처럼 같은 store_id/category로 행을 만들어 커밋해버린다 — 그러면
    get_or_create_scenario가 뒤이어 시도하는 자신의 INSERT가 실제 sqlite
    UNIQUE 제약 위반(IntegrityError)에 부딪히고, except 블록이 그걸 삼키고
    롤백한 뒤 방금 커밋된 행을 재조회해서 반환하는지 검증한다."""
    store = seeded_user["store"]
    concurrent_winner = {}

    def _sneaky_call_haiku(system, user, **kw):
        # get_or_create_scenario의 첫 SELECT는 이미 통과한 뒤, 아직 자신의
        # INSERT/커밋 전인 시점 — 바로 이 틈에 "동시 요청"이 먼저 커밋해버린
        # 상황을 만든다.
        winner = OnboardingScenario(
            store_id=store.id, category="hygiene",
            virtual_review_text="동시 요청이 먼저 만든 가상 리뷰",
            draft_text="동시 요청이 먼저 만든 초안",
            status="pending", created_at=datetime.now(timezone.utc),
        )
        db_session.add(winner)
        db_session.commit()
        concurrent_winner["row"] = winner
        return "이 값은 쓰이지 않는다 — winner가 이미 커밋됐다"

    monkeypatch.setattr(onboarding.client, "call_haiku", _sneaky_call_haiku)
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "초안")

    scenario = onboarding.get_or_create_scenario(db_session, store, "hygiene")

    # 500(처리되지 않은 IntegrityError)이 아니라 먼저 커밋된 "동시 요청"의
    # 행을 그대로 반환해야 하고, 중복 행이 남아있으면 안 된다.
    assert scenario.id == concurrent_winner["row"].id
    assert scenario.virtual_review_text == "동시 요청이 먼저 만든 가상 리뷰"
    count = db_session.query(OnboardingScenario).filter_by(store_id=store.id, category="hygiene").count()
    assert count == 1


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
