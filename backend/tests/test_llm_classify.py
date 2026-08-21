import pytest

from app.llm import classify


def test_classify_review_parses_valid_response(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "hygiene", "is_sensitive": true, "sentiment_conflict": false}',
    )
    result = classify.classify_review("이물질이 나왔어요", 5)
    assert result.category == "hygiene"
    assert result.is_sensitive is True
    assert result.sentiment_conflict is False


def test_classify_review_no_issue_case(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "no_issue", "is_sensitive": false, "sentiment_conflict": false}',
    )
    result = classify.classify_review("정말 맛있어요!", 5)
    assert result.category == "no_issue"


def test_classify_review_raises_on_invalid_category(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '{"category": "unknown_thing", "is_sensitive": false, "sentiment_conflict": false}',
    )
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_raises_on_malformed_json(monkeypatch):
    monkeypatch.setattr(classify.client, "call_haiku", lambda system, user, **kw: "not json")
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_raises_when_api_call_fails(monkeypatch):
    def _raise(system, user, **kw):
        raise RuntimeError("네트워크 오류")

    monkeypatch.setattr(classify.client, "call_haiku", _raise)
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_raises_on_valid_json_non_dict(monkeypatch):
    monkeypatch.setattr(classify.client, "call_haiku", lambda system, user, **kw: "[1, 2, 3]")
    with pytest.raises(classify.ClassificationError):
        classify.classify_review("...", 3)


def test_classify_review_strips_markdown_code_fence(monkeypatch):
    # 실측 확인(2026-08-21): Haiku가 프롬프트 지시("코드 블록 없이")를
    # 무시하고 응답을 ```json ... ```로 감싸는 경우가 실제로 있었다.
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '```json\n{"category": "food_quality", "is_sensitive": false, "sentiment_conflict": false}\n```',
    )
    result = classify.classify_review("닭이 딱딱해요", 1)
    assert result.category == "food_quality"


def test_classify_review_strips_bare_code_fence_without_language_tag(monkeypatch):
    monkeypatch.setattr(
        classify.client, "call_haiku",
        lambda system, user, **kw: '```\n{"category": "service", "is_sensitive": false, "sentiment_conflict": false}\n```',
    )
    result = classify.classify_review("응대가 별로예요", 2)
    assert result.category == "service"
