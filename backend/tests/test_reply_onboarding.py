from datetime import date, datetime, timedelta, timezone

from app.llm import generate, onboarding
from app.models import GoldenExample, OnboardingScenario

_ALL_CATEGORIES = {"food_quality", "delivery", "hygiene", "service", "price", "missing_or_wrong_item"}


def _patch_llm(monkeypatch, virtual_review="가상 리뷰 본문", draft_text="마중물 초안"):
    monkeypatch.setattr(onboarding.client, "call_haiku", lambda system, user, **kw: virtual_review)
    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: draft_text)


def test_wizard_returns_all_uncovered_categories(client, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    assert res.status_code == 200
    categories = {row["category"] for row in res.json()}
    assert categories == _ALL_CATEGORIES


def test_wizard_excludes_covered_categories(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    db_session.add(GoldenExample(
        store_id=seeded_user["store"].id, category="hygiene", review_text="옛날", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    categories = {row["category"] for row in res.json()}
    assert "hygiene" not in categories
    assert len(categories) == 5


def test_wizard_returns_empty_when_fully_covered(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    for c in _ALL_CATEGORIES:
        db_session.add(GoldenExample(
            store_id=seeded_user["store"].id, category=c, review_text="옛날", reply_text="옛날 답글",
            is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    res = client.post("/reply-onboarding/wizard", headers=auth_headers)
    assert res.json() == []


def test_today_limits_to_three_scenarios(client, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    res = client.get("/reply-onboarding/today", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json()) == 3


def test_today_is_stable_within_the_same_day(client, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    first = client.get("/reply-onboarding/today", headers=auth_headers).json()
    second = client.get("/reply-onboarding/today", headers=auth_headers).json()
    assert [row["id"] for row in first] == [row["id"] for row in second]


def test_today_excludes_already_answered_same_day_scenario(client, seeded_user, auth_headers, reply_styles, monkeypatch):
    from app.routers import reply_onboarding as reply_onboarding_mod

    _patch_llm(monkeypatch)
    # save_final_reply와 마찬가지로 답변 시 트리거되는 스타일 프로파일
    # 재생성 백그라운드 태스크는 자체 SessionLocal(실 Postgres)을 열므로,
    # 이 테스트가 관심 없는 부수효과라면 test_reviews.py의 기존 관례대로
    # no-op으로 막는다.
    monkeypatch.setattr(reply_onboarding_mod, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(reply_onboarding_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    monkeypatch.setattr(reply_onboarding_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    first = client.get("/reply-onboarding/today", headers=auth_headers).json()
    answered_id = first[0]["id"]
    client.post(f"/reply-onboarding/scenarios/{answered_id}/answer", json={"content": "실제 답글"}, headers=auth_headers)

    second = client.get("/reply-onboarding/today", headers=auth_headers).json()
    assert answered_id not in [row["id"] for row in second]
    assert len(second) == 2


def test_today_prioritizes_never_shown_categories_over_previously_shown(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    first_day = client.get("/reply-onboarding/today", headers=auth_headers).json()
    first_categories = {row["category"] for row in first_day}
    assert len(first_categories) == 3

    for row in first_day:
        client.post(f"/reply-onboarding/scenarios/{row['id']}/skip", headers=auth_headers)

    # "다음날"처럼 shown_on을 하루 전으로 되돌려 "오늘 아직 안 보여준" 상태를 재현한다.
    yesterday = date.today() - timedelta(days=1)
    for row in first_day:
        scenario = db_session.get(OnboardingScenario, row["id"])
        scenario.shown_on = yesterday
    db_session.commit()

    second_day = client.get("/reply-onboarding/today", headers=auth_headers).json()
    second_categories = {row["category"] for row in second_day}
    assert second_categories.isdisjoint(first_categories)
    assert first_categories | second_categories == _ALL_CATEGORIES


def test_today_rotates_through_all_categories_even_when_wizard_created_them_all_upfront(
    client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch,
):
    # 실제 배포 플로우 재현: 배민 로그인 직후 /wizard가 6개 카테고리 전부를
    # 한 번에 만든다 — 이러면 never_shown이 처음부터 영구히 비고, previously_shown이
    # created_at으로 정렬되면(버그) 매번 VALID_CATEGORIES 순서의 앞 3개
    # (food_quality, delivery, hygiene)만 반복 노출되고 나머지 3개는 절대
    # 안 나온다.
    _patch_llm(monkeypatch)
    client.post("/reply-onboarding/wizard", headers=auth_headers)

    seen_categories: set[str] = set()
    for day_offset in range(1, 4):
        day_rows = client.get("/reply-onboarding/today", headers=auth_headers).json()
        day_categories = {row["category"] for row in day_rows}
        assert len(day_categories) == 3
        seen_categories |= day_categories

        for row in day_rows:
            client.post(f"/reply-onboarding/scenarios/{row['id']}/skip", headers=auth_headers)

        # "다음날"을 재현하려고 이 배치의 shown_on을 과거로 되돌린다 — 오늘
        # 다시 부르면 already_shown(shown_on==today)이 비어서 today가 매번
        # 새로 우선순위를 계산하게 된다.
        backdated = date.today() - timedelta(days=day_offset)
        for row in day_rows:
            scenario = db_session.get(OnboardingScenario, row["id"])
            scenario.shown_on = backdated
        db_session.commit()

    assert seen_categories == _ALL_CATEGORIES


def test_answer_promotes_to_golden_example_and_triggers_style_refresh(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    from app.routers import reply_onboarding as reply_onboarding_mod

    _patch_llm(monkeypatch)
    refreshed = []
    monkeypatch.setattr(reply_onboarding_mod, "refresh_store_style_profile_background", lambda store_id: refreshed.append(store_id))
    monkeypatch.setattr(reply_onboarding_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)

    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")

    res = client.post(
        f"/reply-onboarding/scenarios/{target['id']}/answer",
        json={"content": "실제로 이렇게 답할게요"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "answered"

    example = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").one()
    assert example.reply_text == "실제로 이렇게 답할게요"
    assert example.review_text == target["virtual_review_text"]
    assert example.is_manual is True
    assert example.is_synthetic is False
    assert example.source == "onboarding"
    assert refreshed == [seeded_user["store"].id]


def test_answer_promotes_even_when_identical_to_draft(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    from app.routers import reply_onboarding as reply_onboarding_mod

    _patch_llm(monkeypatch, draft_text="이대로 괜찮아요")
    monkeypatch.setattr(reply_onboarding_mod, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(reply_onboarding_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")
    assert target["draft_text"] == "이대로 괜찮아요"

    client.post(
        f"/reply-onboarding/scenarios/{target['id']}/answer",
        json={"content": "이대로 괜찮아요"},
        headers=auth_headers,
    )

    # save_final_reply(코어 설계)와 달리 diff 비교 없이 항상 승격한다 — 온보딩은
    # 애초에 사장님이 검토·제출한 것이므로 초안과 같아도 진짜 데이터로 취급한다.
    count = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").count()
    assert count == 1


def test_answer_already_answered_returns_409(client, seeded_user, auth_headers, reply_styles, monkeypatch):
    from app.routers import reply_onboarding as reply_onboarding_mod

    _patch_llm(monkeypatch)
    monkeypatch.setattr(reply_onboarding_mod, "refresh_store_style_profile_background", lambda store_id: None)
    monkeypatch.setattr(reply_onboarding_mod, "compute_golden_example_embedding_background", lambda golden_example_id: None)
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = scenarios[0]
    client.post(f"/reply-onboarding/scenarios/{target['id']}/answer", json={"content": "답변"}, headers=auth_headers)

    res = client.post(f"/reply-onboarding/scenarios/{target['id']}/answer", json={"content": "또 답변"}, headers=auth_headers)
    assert res.status_code == 409


def test_skip_does_not_promote_and_stays_available_for_rescan(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    _patch_llm(monkeypatch)
    scenarios = client.post("/reply-onboarding/wizard", headers=auth_headers).json()
    target = next(s for s in scenarios if s["category"] == "hygiene")

    res = client.post(f"/reply-onboarding/scenarios/{target['id']}/skip", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"

    count = db_session.query(GoldenExample).filter_by(store_id=seeded_user["store"].id, category="hygiene").count()
    assert count == 0

    still_uncovered = onboarding.find_uncovered_categories(db_session, seeded_user["store"].id)
    assert "hygiene" in still_uncovered


def test_scenario_action_404_for_other_users_scenario(client, db_session, seeded_user, auth_headers, reply_styles, monkeypatch):
    from app.auth import hash_password
    from app.models import Store, User

    _patch_llm(monkeypatch)
    other_user = User(
        email="other@dris.kr", password_hash=hash_password("x"), nickname="다른사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()
    other_store = Store(user_id=other_user.id, name="다른가게", category="분식", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.commit()

    other_scenario = onboarding.get_or_create_scenario(db_session, other_store, "hygiene")

    res = client.post(
        f"/reply-onboarding/scenarios/{other_scenario.id}/answer", json={"content": "몰래 답변"}, headers=auth_headers,
    )
    assert res.status_code == 404
