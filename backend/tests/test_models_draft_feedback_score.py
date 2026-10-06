from datetime import datetime, timezone

from app.models import DraftFeedbackScore, OnboardingScenario, ReviewReply


def test_review_reply_has_nullable_trace_id_column(db_session, seeded_user, platforms):
    from app.models import Review

    store = seeded_user["store"]
    platform = platforms["baemin"]
    review = Review(
        store_id=store.id, platform_id=platform.id, menu_summary="치킨", rating=5,
        content="맛있어요", customer_nickname="손님", customer_order_count=1,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()

    reply = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=None,
        content="감사합니다", trace_id="11111111-1111-1111-1111-111111111111",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(reply)
    db_session.commit()

    fetched = db_session.get(ReviewReply, reply.id)
    assert fetched.trace_id == "11111111-1111-1111-1111-111111111111"


def test_onboarding_scenario_has_nullable_trace_id_column(db_session, seeded_user):
    store = seeded_user["store"]
    scenario = OnboardingScenario(
        store_id=store.id, category="delivery", virtual_review_text="배달이 늦었어요",
        draft_text="불편드려 죄송합니다", trace_id="22222222-2222-2222-2222-222222222222",
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db_session.add(scenario)
    db_session.commit()

    fetched = db_session.get(OnboardingScenario, scenario.id)
    assert fetched.trace_id == "22222222-2222-2222-2222-222222222222"


def test_draft_feedback_score_round_trips(db_session, seeded_user):
    store = seeded_user["store"]
    score = DraftFeedbackScore(
        store_id=store.id, category="food_quality", similarity_score=0.8421,
        trace_id="33333333-3333-3333-3333-333333333333",
        source_review_id=None, source_scenario_id=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(score)
    db_session.commit()

    fetched = db_session.get(DraftFeedbackScore, score.id)
    assert fetched.category == "food_quality"
    assert float(fetched.similarity_score) == 0.8421
