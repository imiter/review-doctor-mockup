"""리뷰 자동 분류 — 불만 유형(category), 민감도(is_sensitive), 별점-텍스트
불일치(sentiment_conflict)를 Haiku 1회 호출로 함께 판정한다. 리뷰가
동기화되는 시점(review_sync.py)에 호출돼야 사장님이 리뷰를 열어보지
않아도 민감 리뷰 알림이 뜬다."""

import json
from dataclasses import dataclass

from app.llm import client

VALID_CATEGORIES = (
    "food_quality", "delivery", "hygiene", "service",
    "price", "missing_or_wrong_item", "no_issue",
)

_SYSTEM_PROMPT = """너는 배달 음식점 리뷰를 분석하는 분류기다. 아래 리뷰를 읽고 JSON으로만 답하라.

카테고리(정확히 하나만 선택):
- food_quality: 맛, 온도, 양에 대한 불만
- delivery: 배달 지연, 파손에 대한 불만
- hygiene: 위생, 이물질, 곰팡이 등 안전 관련 불만
- service: 응대, 태도에 대한 불만
- price: 가격에 대한 불만
- missing_or_wrong_item: 누락, 오배송
- no_issue: 위 어디에도 해당하는 불만이 없음 (칭찬만 있거나 중립적)

is_sensitive: 위생/이물질/알레르기/안전 관련 언급이 있어 신중한 대응이
필요하면 true. 단순 맛 불만 등은 false.

sentiment_conflict: 별점과 리뷰 내용의 감정이 서로 어긋나면 true. 예:
별점은 4~5점인데 내용에 뚜렷한 불만이 섞여 있는 경우. 별점이 낮은데
내용도 부정적인 건 "일치"이므로 false.

JSON 형식으로만 답하라: {"category": "...", "is_sensitive": true/false, "sentiment_conflict": true/false}"""


class ClassificationError(Exception):
    pass


@dataclass(frozen=True)
class ReviewClassification:
    category: str
    is_sensitive: bool
    sentiment_conflict: bool


def classify_review(content: str, rating: int) -> ReviewClassification:
    user_message = f'리뷰: "{content}"\n별점: {rating}'
    try:
        raw = client.call_haiku(_SYSTEM_PROMPT, user_message)
        data = json.loads(raw)
    except Exception as e:
        raise ClassificationError(f"리뷰 분류 API 호출 실패: {e}") from e

    category = data.get("category")
    if category not in VALID_CATEGORIES:
        raise ClassificationError(f"알 수 없는 category: {category!r}")
    return ReviewClassification(
        category=category,
        is_sensitive=bool(data.get("is_sensitive", False)),
        sentiment_conflict=bool(data.get("sentiment_conflict", False)),
    )
