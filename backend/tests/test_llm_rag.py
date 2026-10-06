from datetime import datetime, timedelta, timezone

from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import GoldenExample, Review


def _make_example(db_session, store_id, *, category, source, created_at, source_review_id=None, needs_confirmation=False):
    ex = GoldenExample(
        store_id=store_id, category=category,
        review_text="리뷰", reply_text="답글",
        is_manual=True, is_synthetic=False, source=source,
        source_review_id=source_review_id, created_at=created_at,
        needs_confirmation=needs_confirmation,
    )
    db_session.add(ex)
    db_session.flush()
    return ex


def _make_review(db_session, store_id, platform_id, *, category="no_issue", sentiment_conflict=False, created_at):
    review = Review(
        store_id=store_id, platform_id=platform_id, menu_summary="치킨",
        rating=5, content="리뷰 본문", customer_nickname="손님",
        category=category, sentiment_conflict=sentiment_conflict, created_at=created_at,
    )
    db_session.add(review)
    db_session.flush()
    return review


def _pad_reviews(db_session, store_id, platform_id, count, *, created_at):
    """신호 (a)("그 리뷰가 들어온 시점의 누적 리뷰 ≤ 3건")를 끄기 위해 더미
    리뷰를 미리 채운다 — 신호 (b)/(c)만 단독으로 검증하려면 이게 필요하다."""
    for _ in range(count):
        _make_review(db_session, store_id, platform_id, created_at=created_at)


# ---------------------------------------------------------------------------
# 1단계(고신뢰) 신호 3개를 하나씩 단독으로 검증한다 — 셋 중 아무거나 하나만
# 있어도 1단계로 올라가야 하므로, 검증하려는 신호 하나만 켜고 나머지 둘은
# 명시적으로 끈 상태를 만든다.
# ---------------------------------------------------------------------------
def test_tier1_signal_complaint_category(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))  # 신호 (a) 끔
    signal_review = _make_review(db_session, sid, pid, category="delivery", created_at=now)  # (b)만 켬
    high = _make_example(db_session, sid, category="delivery", source="organic",
                         created_at=now - timedelta(days=2), source_review_id=signal_review.id)
    _make_example(db_session, sid, category="delivery", source="organic", created_at=now)  # 더 최신 = 신호 없음
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=1)

    assert [r.id for r in result] == [high.id]


def test_tier1_signal_review_count_is_measured_at_review_time_not_now(db_session, seeded_user, platforms):
    """신호 (a)는 "그 리뷰가 들어온 시점"의 누적 리뷰 수다 — "지금" 기준도,
    "통산" 기준도 아니다. 아래 가게는 지금 리뷰가 11건이지만, 연결된 리뷰는
    가게 최초 리뷰(그 시점 누적 1건)라 1단계여야 한다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    early_review = _make_review(db_session, sid, pid, created_at=now - timedelta(days=100))  # (a)만 켬
    _pad_reviews(db_session, sid, pid, 10, created_at=now)  # 지금은 11건 — "지금" 기준이면 신호 꺼짐
    high = _make_example(db_session, sid, category="food_quality", source="organic",
                         created_at=now - timedelta(days=2), source_review_id=early_review.id)
    _make_example(db_session, sid, category="food_quality", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "food_quality", "쿼리", limit=1)

    assert [r.id for r in result] == [high.id]


def test_tier1_signal_sentiment_conflict(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))  # (a) 끔
    conflict_review = _make_review(db_session, sid, pid, category="no_issue",  # (b) 끔
                                   sentiment_conflict=True, created_at=now)    # (c)만 켬
    high = _make_example(db_session, sid, category="no_issue", source="organic",
                         created_at=now - timedelta(days=2), source_review_id=conflict_review.id)
    _make_example(db_session, sid, category="no_issue", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "no_issue", "쿼리", limit=1)

    assert [r.id for r in result] == [high.id]


def test_linked_review_without_any_signal_stays_in_tier2(db_session, seeded_user, platforms):
    """리뷰가 연결돼 있어도 신호 3개가 전부 없으면 1단계가 아니다 — 연결만
    으로 1단계로 올라가면 신호 판정 자체가 무의미해진다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))
    plain_review = _make_review(db_session, sid, pid, category="no_issue", created_at=now)
    linked_no_signal = _make_example(db_session, sid, category="no_issue", source="organic",
                                     created_at=now, source_review_id=plain_review.id)
    onboarding = _make_example(db_session, sid, category="no_issue", source="onboarding",
                               created_at=now - timedelta(days=1))
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "no_issue", "쿼리", limit=2)

    # 1단계는 비어 있고, 2단계(신호 없는 organic) → 3단계(onboarding) 순서
    assert [r.id for r in result] == [linked_no_signal.id, onboarding.id]


def test_needs_confirmation_excluded_from_tier1(db_session, seeded_user, platforms):
    """needs_confirmation=True인 organic 답글은 고신뢰 신호가 있어도
    1단계에서 제외된다(경로 C에서 말투 이상치로 찍힌 미확인 행,
    promote_direct_reply_to_golden_example 참고). 미확인 예시를 일부러 더
    최신으로 만들어 뒀다 — 1단계에 포함된다면 최신순으로 먼저 나왔을
    것이므로, confirmed 예시가 먼저 나오는 것 자체가 배제를 증명한다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))  # 신호 (a) 끔
    confirmed_signal_review = _make_review(db_session, sid, pid, category="delivery", created_at=now - timedelta(days=5))
    unconfirmed_signal_review = _make_review(db_session, sid, pid, category="delivery", created_at=now)
    confirmed = _make_example(db_session, sid, category="delivery", source="organic",
                              created_at=now - timedelta(days=3), source_review_id=confirmed_signal_review.id)
    unconfirmed = _make_example(db_session, sid, category="delivery", source="organic",
                                created_at=now, source_review_id=unconfirmed_signal_review.id,
                                needs_confirmation=True)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=1)

    assert [r.id for r in result] == [confirmed.id]


def test_needs_confirmation_falls_to_tier2_when_nothing_else_fills_limit(db_session, seeded_user, platforms):
    """needs_confirmation=True 행은 1단계에서 빠지지만 완전히 버려지지는
    않는다 — 다른 1·2단계 예시가 없어 limit을 못 채우면 2단계로 내려와
    여전히 반환된다(불확실한 데이터는 플래그만 세우고 버리지 않는다는
    원칙)."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))
    signal_review = _make_review(db_session, sid, pid, category="hygiene", created_at=now)
    unconfirmed = _make_example(db_session, sid, category="hygiene", source="organic",
                                created_at=now, source_review_id=signal_review.id,
                                needs_confirmation=True)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=3)

    assert [r.id for r in result] == [unconfirmed.id]


# ---------------------------------------------------------------------------
# 3단계 우선순위 전체
# ---------------------------------------------------------------------------
def test_three_tiers_drain_in_priority_order(db_session, seeded_user, platforms):
    """단계가 최신순을 이긴다 — 일부러 낮은 단계 예시를 더 최신으로 만들어
    둬서, 단계를 적용하지 않으면 순서가 정확히 거꾸로 나오게 했다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))
    signal_review = _make_review(db_session, sid, pid, category="hygiene", created_at=now)
    plain_review = _make_review(db_session, sid, pid, category="no_issue", created_at=now)
    tier1 = _make_example(db_session, sid, category="hygiene", source="organic",
                          created_at=now - timedelta(days=2), source_review_id=signal_review.id)
    tier2 = _make_example(db_session, sid, category="hygiene", source="organic",
                          created_at=now - timedelta(days=1), source_review_id=plain_review.id)
    tier3 = _make_example(db_session, sid, category="hygiene", source="onboarding", created_at=now)
    db_session.commit()

    assert [r.id for r in fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=3)] == [
        tier1.id, tier2.id, tier3.id,
    ]
    assert [r.id for r in fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=1)] == [tier1.id]
    assert [r.id for r in fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=2)] == [
        tier1.id, tier2.id,
    ]


def test_onboarding_is_not_used_when_higher_tiers_fill_the_limit(db_session, seeded_user, platforms):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    signal_review = _make_review(db_session, sid, pid, category="price", created_at=now)
    _make_example(db_session, sid, category="price", source="organic",
                  created_at=now, source_review_id=signal_review.id)
    _make_example(db_session, sid, category="price", source="onboarding", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "price", "쿼리", limit=1)

    assert [r.source for r in result] == ["organic"]


def test_falls_back_to_onboarding_when_organic_insufficient(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    organic = _make_example(db_session, sid, category="hygiene", source="organic", created_at=now)
    onboarding = _make_example(db_session, sid, category="hygiene", source="onboarding", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "hygiene", "쿼리", limit=2)

    assert [r.id for r in result] == [organic.id, onboarding.id]


def test_backfill_and_organic_direct_share_the_organic_tiers(db_session, seeded_user, platforms):
    """'사람이 직접 쓴 답글'로 간주되는 세 소스(organic/organic_direct/
    backfill)는 같은 묶음이고, 1·2단계 구분은 소스 종류가 아니라 신호로만
    가른다 — 신호 있는 backfill이 신호 없는 organic보다 먼저 나와야 한다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    now = datetime.now(timezone.utc)
    _pad_reviews(db_session, sid, pid, 5, created_at=now - timedelta(days=10))
    signal_review = _make_review(db_session, sid, pid, category="service", created_at=now)
    backfill_with_signal = _make_example(db_session, sid, category="service", source="backfill",
                                         created_at=now - timedelta(days=3), source_review_id=signal_review.id)
    organic_direct_no_signal = _make_example(db_session, sid, category="service",
                                             source="organic_direct", created_at=now - timedelta(days=1))
    organic_no_signal = _make_example(db_session, sid, category="service", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "service", "쿼리", limit=3)

    assert result[0].id == backfill_with_signal.id
    # 2단계 안에서는 기존 그대로 최신순
    assert [r.id for r in result[1:]] == [organic_no_signal.id, organic_direct_no_signal.id]


def test_legacy_synthetic_rows_are_never_returned(db_session, seeded_user):
    """synthetic 메커니즘은 제거됐다(2026-10-06) — 과거에 남아있을 수 있는
    행이 3단계 어디에도 끼지 않는 것을 고정한다."""
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="delivery", source="synthetic", created_at=now)
    db_session.commit()

    assert fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=3) == []


def test_fetch_golden_examples_filters_by_category(db_session, seeded_user):
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    _make_example(db_session, sid, category="hygiene", source="organic", created_at=now)
    _make_example(db_session, sid, category="delivery", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=3)

    assert len(result) == 1
    assert result[0].category == "delivery"


def test_fetch_golden_examples_filters_by_store(db_session, seeded_user, platforms):
    """다른 매장의 예시가 섞이면 안 된다 — 신호 계산(그 시점의 누적 리뷰
    수)도 반드시 이 매장 리뷰만 센다."""
    from app.models import Store

    sid = seeded_user["store"].id
    other = Store(user_id=seeded_user["user"].id, name="다른매장", category="치킨",
                  created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    now = datetime.now(timezone.utc)
    _make_example(db_session, other.id, category="delivery", source="organic", created_at=now)
    db_session.commit()

    assert fetch_golden_examples(db_session, sid, "delivery", "쿼리", limit=3) == []


def test_fetch_golden_examples_falls_back_to_recency_when_embedding_unavailable(db_session, seeded_user):
    """VOYAGE_API_KEY가 없어 embed_query가 실패하면(이 fixture 스위트는
    _no_voyage_key로 항상 키를 지운다) 기존처럼 최신순으로 폴백해야
    한다 — 임베딩 API 가용성이 답글 생성 자체를 막으면 안 된다."""
    sid = seeded_user["store"].id
    now = datetime.now(timezone.utc)
    older = _make_example(db_session, sid, category="food_quality", source="organic",
                          created_at=now - timedelta(days=5))
    newer = _make_example(db_session, sid, category="food_quality", source="organic", created_at=now)
    db_session.commit()

    result = fetch_golden_examples(db_session, sid, "food_quality", "쿼리", limit=2)

    assert [r.id for r in result] == [newer.id, older.id]


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
