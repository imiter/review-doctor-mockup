from datetime import datetime, timezone

from sqlalchemy import select

from app.llm import generate
from app.models import BaeminShopBrand, GoldenExample, Review, StorePlatformConnection, StoreStyleProfile


def test_generate_ai_reply_includes_style_profile_and_examples(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="- 구체적 원인을 설명한다", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="옛날 리뷰", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=3, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return "죄송합니다, 확인하겠습니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert result == "죄송합니다, 확인하겠습니다."
    assert "구체적 원인을 설명한다" in captured["system"]
    assert "옛날 리뷰" in captured["system"]
    assert "내용을 그대로 복사하지" in captured["system"]  # 안전장치 지시가 포함됐는지
    assert "재방문" in captured["user"] or "3회" in captured["user"]  # 재방문 고객 정보 반영
    assert "이물질이 나왔어요" in captured["user"]


def test_generate_ai_reply_without_style_profile_uses_fallback_instruction(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=2, content="배달이 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    def _fake_call_sonnet(system, user, **kw):
        return "죄송합니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert result == "죄송합니다."


def test_generate_ai_reply_injects_sensitive_instruction(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "민감" in captured["user"] or "신중" in captured["user"]


def test_generate_ai_reply_omits_complaint_framing_for_no_issue_review(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """no_issue 리뷰는 "불만 유형: no_issue"처럼 없는 불만을 있는 것처럼
    프레이밍하면 안 된다 — 별점 4점에 "조금 더 매우면 맛있을 것 같아요"
    처럼 취향/요청이 섞인 리뷰가 무시당하는 문제(2026-08-24)를 고친
    회귀 테스트."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="숯불양념바베큐치킨", rating=4,
        content="기본맛으로 주문했는데 맵지 않아요~ 조금 더 매우면 맛있을 것 같아요",
        customer_nickname="원투셋", customer_order_count=1, category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "불만 유형: no_issue" not in captured["user"]
    assert "조금 더 매우면 맛있을 것 같아요" in captured["user"]


def test_generate_ai_reply_includes_persona_tone_instruction_when_no_issue(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="맛있게 잘 먹었어요",
        customer_nickname="손님", customer_order_count=1, category="no_issue",
        is_sensitive=False, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert reply_styles.tone_instruction in captured["system"]


def test_generate_ai_reply_overrides_tone_for_non_sensitive_complaint(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """2026-08-31 실사용 확인: food_quality처럼 is_sensitive/sentiment_conflict
    둘 다 아닌 일반 불만 리뷰에도 이모지가 섞여 나온다는 지적을 받고, 톤 오버라이드
    범위를 "no_issue가 아닌 모든 리뷰"로 넓힌 회귀 테스트."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=2, content="배달이 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        is_sensitive=False, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert generate._COMPLAINT_TONE_OVERRIDE in captured["system"]
    assert reply_styles.tone_instruction not in captured["system"]


def test_generate_ai_reply_overrides_tone_when_sensitive(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert generate._COMPLAINT_TONE_OVERRIDE in captured["system"]
    assert reply_styles.tone_instruction not in captured["system"]  # 페르소나 톤이 완전히 대체됐는지


def test_generate_ai_reply_overrides_tone_when_sentiment_conflict(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="배달이 너무 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        is_sensitive=False, sentiment_conflict=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert generate._COMPLAINT_TONE_OVERRIDE in captured["system"]
    assert reply_styles.tone_instruction not in captured["system"]


def test_generate_ai_reply_strips_emoji_from_output_when_sensitive(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """텍스트 지시("이모지 없이")만으로는 few-shot 예시(사장님의 실제 과거
    답글, 이모지 섞인 경우가 많음)와 신호가 충돌해 이모지가 새어나오는
    문제가 실사용으로 확인됐다(2026-08-26) — 최종 출력에서 확정적으로
    한 번 더 걸러낸다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(
        generate.client, "call_sonnet",
        lambda system, user, **kw: "안녕하세요😊 죄송합니다🙏 확인하겠습니다😞",
    )

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "😊" not in result and "🙏" not in result and "😞" not in result
    assert result == "안녕하세요 죄송합니다 확인하겠습니다"


def test_generate_ai_reply_keeps_emoji_for_no_issue_review(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="맛있어요",
        customer_nickname="손님", customer_order_count=1, category="no_issue",
        is_sensitive=False, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(generate.client, "call_sonnet", lambda system, user, **kw: "감사합니다😊")

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert result == "감사합니다😊"  # no_issue(칭찬/무난) 리뷰면 이모지를 그대로 둔다


def test_generate_ai_reply_strips_emoji_from_examples_when_sensitive(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """프롬프트에 넣는 [참고 예시]에서부터 이모지를 지워, 지시문("이모지
    없이")과 모순되는 신호 자체를 없앤다 — 출력만 사후 필터링하면 예시가
    계속 이모지 섞인 톤을 보여줘 다른 부분(문장 구조, 격식)까지 영향을
    줄 수 있다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="예전 리뷰",
        reply_text="안녕하세요😊 죄송합니다🙏",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "😊" not in captured["system"] and "🙏" not in captured["system"]
    assert "안녕하세요 죄송합니다" in captured["system"]  # 예시 내용 자체는 남아있음


def test_generate_ai_reply_grounding_present_even_when_tone_overridden(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """톤이 override돼도 사장님 말투 그라운딩(store_style_profile)과 골든
    예시는 그대로 시스템 프롬프트에 남아있어야 한다 — 톤 레이어는 표면적
    조절일 뿐 그라운딩을 대체하지 않는다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="- 항상 재방문을 유도한다", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "항상 재방문을 유도한다" in captured["system"]


def test_generate_ai_reply_uses_matched_brand_name_not_store_name(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """한 배민 계정에 브랜드가 여러 개 딸려있을 때, 리뷰의 platform_shop_no로
    실제 브랜드명을 찾아 답글에 써야 한다 — Store.name(대표 브랜드) 하나만
    쓰면 다른 브랜드 리뷰에 엉뚱한 이름이 붙는 문제(2026-08-24 실측 확인:
    "블랙닭갈비" 리뷰에 "치킨대장 당고점입니다"가 붙음)를 고친 회귀 테스트."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id

    connection = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == sid)
    )
    db_session.add(BaeminShopBrand(
        connection_id=connection.id, shop_no="14804914",
        shop_name="[음식배달] 블랙닭갈비 노원당고개점 / 고기·구이 14804914",
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="닭갈비", rating=5, content="맛있어요",
        customer_nickname="손님", platform_shop_no="14804914", category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert '"블랙닭갈비 노원당고개점"' in captured["system"]
    assert "치킨대장" not in captured["system"]


def test_generate_ai_reply_falls_back_to_store_name_when_no_brand_match(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """platform_shop_no가 없거나(온보딩 가상 리뷰 등) 매칭되는 브랜드가
    없으면 기존처럼 Store.name으로 폴백한다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="맛있어요",
        customer_nickname="손님", category="no_issue", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert '"치킨대장"' in captured["system"]


def test_generate_ai_reply_includes_matched_menu_composition(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """리뷰의 menu_summary와 이름이 매칭되는 실제 메뉴 구성을 프롬프트에
    포함해야 한다 — "치킨마요는 밥만 많고 고기가 없다"는 불만에 AI가
    실제 구성을 모른 채 틀린 원인을 추측하던 문제(2026-08-26)를 고친
    회귀 테스트."""
    from app.models import BrandMenuInfo, StorePlatformConnection

    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    connection = db_session.scalar(
        select(StorePlatformConnection).where(StorePlatformConnection.store_id == sid)
    )
    db_session.add(BrandMenuInfo(
        connection_id=connection.id, shop_no="14804318",
        store_intro="100% 순살 닭다리살만 씁니다.",
        food_origin="닭고기(국내산)",
        menu_intro="연육염지닭 사용",
        menu_items=[
            {"name": "치킨마요:", "desc": "든든한 한 끼", "composition": "치킨마요[닭다리살 정량]+공기밥", "price": 8900},
        ],
        updated_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨마요", rating=5,
        content="치킨마요에 밥양만 많고 고기가 거의없어서", customer_nickname="손님",
        platform_shop_no="14804318", category="no_issue", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "치킨마요[닭다리살 정량]+공기밥" in captured["system"]
    assert "100% 순살 닭다리살만 씁니다" in captured["system"]


def test_generate_ai_reply_omits_menu_section_when_no_brand_menu_info(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨마요", rating=5, content="맛있어요",
        customer_nickname="손님", platform_shop_no="14804318", category="no_issue",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "가게/메뉴 실제 정보" not in captured["system"]
