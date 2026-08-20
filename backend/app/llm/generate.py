"""문제 리뷰(category != "no_issue")에 대한 RAG 기반 답글 생성. 검색
(app.llm.rag)과 생성(Sonnet)을 조합한다 — 벡터 검색은 쓰지 않는다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import Review, Store, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_CATEGORY_LABELS = {
    "food_quality": "음식 품질(맛/온도/양)",
    "delivery": "배달(지연/파손)",
    "hygiene": "위생/이물질",
    "service": "응대",
    "price": "가격",
    "missing_or_wrong_item": "오배송/누락",
}


def _build_system_prompt(store: Store, style_rules: str, examples) -> str:
    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{ex.reply_text}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    return f"""너는 "{store.name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은
반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""


def _build_user_message(review: Review, category_label: str, repeat_count: int) -> str:
    lines = [
        f"별점: {review.rating}",
        f"불만 유형: {category_label}",
        f'내용: "{review.content}"',
        f"이 고객의 누적 주문 횟수: {review.customer_order_count}회",
    ]
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)


def generate_ai_reply(db: Session, review: Review, store: Store) -> str:
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES

    examples = fetch_golden_examples(db, store.id, review.category, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = _CATEGORY_LABELS.get(review.category, review.category)

    system_prompt = _build_system_prompt(store, style_rules, examples)
    user_message = _build_user_message(review, category_label, repeat_count)
    return client.call_sonnet(system_prompt, user_message, max_tokens=800)
