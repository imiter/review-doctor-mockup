from datetime import datetime, timezone

from app.llm import style_profile
from app.models import GoldenExample, StoreStyleProfile


def _make_example(db_session, store_id, *, is_manual, is_synthetic, category="hygiene"):
    ex = GoldenExample(
        store_id=store_id, category=category, review_text="이물질이 나왔어요",
        reply_text="겉불을 쎄게 조리해서 그런 것 같습니다, 죄송합니다",
        is_manual=is_manual, is_synthetic=is_synthetic, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ex)
    return ex


def test_refresh_creates_profile_from_manual_examples_only(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    _make_example(db_session, sid, is_manual=False, is_synthetic=True)  # 이건 반영되면 안 됨
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "- 구체적 원인을 설명한다\n- 재방문 고객을 언급한다"

    monkeypatch.setattr(style_profile.client, "call_sonnet", _fake_call_sonnet)

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert "구체적 원인" in profile.rules
    assert profile.generated_from_count == 1  # is_synthetic 예시는 제외
    assert "이물질이 나왔어요" in captured["user"]


def test_refresh_excludes_needs_confirmation_examples(db_session, seeded_user, monkeypatch):
    """needs_confirmation=True(경로 C — 배민에 직접 단 답글이 기존 신뢰
    예시 클러스터와 말투가 어긋나는 이상치로 판정된 것)인 예시는
    is_manual=true/is_synthetic=false를 만족해도 스타일 요약 입력에서
    빠져야 한다 — 아직 사람이 확인하지 않은, 진짜 사장님 말투인지 의심되는
    답글이 "이 사장님의 말투" 요약에 섞이면 안 된다."""
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    unconfirmed = GoldenExample(
        store_id=sid, category="hygiene", review_text="의심스러운 리뷰 본문",
        reply_text="의심스러운 답글 본문",
        is_manual=True, is_synthetic=False, source="organic_direct",
        needs_confirmation=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(unconfirmed)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "- 구체적 원인을 설명한다"

    monkeypatch.setattr(style_profile.client, "call_sonnet", _fake_call_sonnet)

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.generated_from_count == 1  # needs_confirmation 예시는 제외
    assert "의심스러운 리뷰 본문" not in captured["user"]
    assert "이물질이 나왔어요" in captured["user"]  # confirmed 예시는 그대로 반영


def test_refresh_updates_existing_profile(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="옛날 규칙", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()

    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "새 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.rules == "새 규칙"


def test_refresh_noop_when_no_manual_examples(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    calls = []
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: calls.append(1) or "무시됨")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    assert calls == []  # 예시가 없으면 API 호출 자체를 안 함
    assert db_session.get(StoreStyleProfile, (sid, "hygiene")) is None


def test_refresh_only_includes_matching_category_examples(db_session, seeded_user, monkeypatch):
    """다른 카테고리(delivery)에 예시가 쌓여도 hygiene 카테고리를
    재생성할 때는 섞여 들어가면 안 된다 — 카테고리별로 완전히 독립된
    캐시 행이어야 한다."""
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False, category="hygiene")
    other = GoldenExample(
        store_id=sid, category="delivery", review_text="배달이 너무 늦었어요",
        reply_text="배달 지연으로 불편을 드려 죄송합니다",
        is_manual=True, is_synthetic=False, source="backfill",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        style_profile.client, "call_sonnet",
        lambda system, user, **kw: captured.setdefault("user", user) and "- 요약",
    )

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    assert "이물질" in captured["user"]
    assert "배달" not in captured["user"]
    assert db_session.get(StoreStyleProfile, (sid, "delivery")) is None  # delivery는 아직 재생성 안 함


def test_refresh_sets_needs_confirmation_when_profile_is_new(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "새 원칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True


def test_refresh_sets_needs_confirmation_when_rules_text_changes(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="옛날 규칙", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "완전히 다른 새 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True


def test_refresh_does_not_reset_needs_confirmation_when_rules_text_unchanged(db_session, seeded_user, monkeypatch):
    """이미 확인 완료(needs_confirmation=False)인 원칙이, 다시 돌려도
    똑같은 텍스트로 재생성되면 — 사장님이 또 확인할 필요가 없으므로 —
    플래그를 다시 세우면 안 된다("원칙이 실제로 바뀔 때만 뜬다")."""
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="변하지 않는 규칙", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "변하지 않는 규칙")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is False


def test_refresh_does_not_flag_when_rules_are_semantically_equivalent_paraphrase(db_session, seeded_user, monkeypatch):
    """call_sonnet의 temperature 기본값(1.0) 때문에 같은 예시로 다시
    요약해도 표현만 바뀌는 경우가 흔하다 — 바이트는 다르지만 의미가
    거의 같으면(코사인 유사도가 임계값 이상) needs_confirmation을
    세우면 안 된다(2026-10-07 최종 리뷰)."""
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="원래 문장", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "표현만 다른 같은 뜻 문장")
    monkeypatch.setattr(style_profile, "embed_documents", lambda texts: [[1.0, 0.0], [0.99, 0.01]])

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is False


def test_refresh_flags_when_rules_are_semantically_different(db_session, seeded_user, monkeypatch):
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="원래 문장", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "완전히 다른 뜻의 새 문장")
    monkeypatch.setattr(style_profile, "embed_documents", lambda texts: [[1.0, 0.0], [0.0, 1.0]])

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True


def test_refresh_falls_back_to_flagging_when_embedding_fails(db_session, seeded_user, monkeypatch):
    """Voyage 호출이 실패하면(키 미설정 등) 보수적으로 "바뀌었다"로
    처리한다 — 확인 기회를 놓치는 것보다 과하게 뜨는 쪽이 안전하다.
    VOYAGE_API_KEY를 몽키패치로 지울 필요 없다 — conftest의 autouse
    _no_voyage_key 픽스처가 이미 매 테스트마다 지운다."""
    sid = seeded_user["store"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, category="hygiene", rules="원래 문장", generated_from_count=1,
        needs_confirmation=False, updated_at=datetime.now(timezone.utc),
    ))
    _make_example(db_session, sid, is_manual=True, is_synthetic=False)
    db_session.commit()
    monkeypatch.setattr(style_profile.client, "call_sonnet", lambda system, user, **kw: "다른 표현의 문장")

    style_profile.refresh_store_style_profile(db_session, sid, "hygiene")

    profile = db_session.get(StoreStyleProfile, (sid, "hygiene"))
    assert profile.needs_confirmation is True
