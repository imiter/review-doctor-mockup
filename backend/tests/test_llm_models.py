from datetime import datetime, timezone

import pytest

from app.models import GoldenExample, OnboardingScenario, ReplyStyle, Review, StoreStyleProfile


def test_review_classification_columns_default(db_session, seeded_user, platforms):
    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=5, content="맛있어요", customer_nickname="손님",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    row = db_session.query(Review).filter_by(id=review.id).one()
    assert row.category == "no_issue"
    assert row.is_sensitive is False
    assert row.sentiment_conflict is False


def test_golden_example_round_trips(db_session, seeded_user):
    ex = GoldenExample(
        store_id=seeded_user["store"].id, category="hygiene",
        review_text="이물질이 나왔어요", reply_text="죄송합니다, 확인하겠습니다",
        is_manual=True, is_synthetic=False, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    db_session.commit()

    row = db_session.query(GoldenExample).filter_by(id=ex.id).one()
    assert row.category == "hygiene"
    assert row.is_manual is True
    assert row.source == "backfill"


def test_store_style_profile_round_trips(db_session, seeded_user):
    profile = StoreStyleProfile(
        store_id=seeded_user["store"].id, rules="- 구체적 원인을 설명한다\n- 재방문 고객을 언급한다",
        generated_from_count=5, updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    row = db_session.query(StoreStyleProfile).filter_by(store_id=seeded_user["store"].id).one()
    assert row.generated_from_count == 5


def test_onboarding_scenario_round_trips(db_session, seeded_user):
    scenario = OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="포장에서 냄새가 나요", draft_text="죄송합니다, 확인하겠습니다",
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    row = db_session.query(OnboardingScenario).filter_by(id=scenario.id).one()
    assert row.category == "hygiene"
    assert row.status == "pending"
    assert row.shown_on is None


def test_onboarding_scenario_unique_per_store_and_category(db_session, seeded_user):
    from sqlalchemy.exc import IntegrityError

    db_session.add(OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="첫 번째", draft_text="첫 번째 초안",
        status="pending", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    db_session.add(OnboardingScenario(
        store_id=seeded_user["store"].id, category="hygiene",
        virtual_review_text="두 번째", draft_text="두 번째 초안",
        status="pending", created_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_reply_style_tone_instruction_round_trips(db_session):
    style = ReplyStyle(
        name="테스트 스타일", description="테스트용",
        template_high="{nickname}님 감사합니다.",
        template_mid="{nickname}님 아쉬워요.",
        template_low="{nickname}님 죄송합니다.",
        tone_instruction="이모지 없이 담백하게 작성하세요.",
    )
    db_session.add(style)
    db_session.commit()

    row = db_session.query(ReplyStyle).filter_by(id=style.id).one()
    assert row.tone_instruction == "이모지 없이 담백하게 작성하세요."
