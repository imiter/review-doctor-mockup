"""모든 리뷰(칭찬/무난 포함)에 대한 RAG 기반 답글 생성. 검색(app.llm.rag)과
생성(Sonnet)을 조합한다 — 벡터 검색은 쓰지 않는다.

원래는 category == "no_issue"(칭찬/무난) 리뷰만 무료 템플릿 치환으로
처리하고 이 RAG 경로는 문제 리뷰에만 탔다. 그런데 실사용 중 "기본맛으로
주문했는데 맵지 않아요, 조금 더 매우면 맛있을 것 같아요"처럼 별점은
높지만 구체적인 피드백이 담긴 리뷰에도 템플릿이 리뷰 내용과 무관한
정형 문구("맛있게 드셨다니 다행입니다")를 그대로 붙이는 문제가
확인됐다(2026-08-24) — 카테고리 분류가 "명백한 불만"이 아니면 전부
no_issue로 묶이기 때문에, 템플릿 경로는 이런 리뷰의 구체적 내용을 전혀
반영하지 못한다. 그래서 no_issue를 포함한 모든 리뷰가 이 RAG 경로를
타도록 바꿨다 — 사장님이 실제로 답글을 원하는 이상 항상 사장님의 학습된
말투로 응답하는 게 "찐사장님 말투"라는 이 기능의 원래 목적에 맞다는
판단(사용자 확인). reply_styles.template_high/mid/low 컬럼은 이제 이
경로에서 더 이상 읽지 않는다 — 컬럼 자체는 되돌릴 여지를 남기려고 스키마에
그대로 뒀다(DROP 안 함).

페르소나(reply_styles.tone_instruction)는 표면적 톤(이모지 사용량, 격식
수준)만 조절하는 얇은 레이어다 — 원인 설명·사과의 실질적 근거는 항상
store_style_profile(사장님 말투 그라운딩)과 골든 예시에서만 온다. 위생/안전
민감 사안이거나 별점-내용이 어긋나는 리뷰는 페르소나 선택과 무관하게
_SENSITIVE_TONE_OVERRIDE로 강제 전환한다(설계 문서
2026-08-24-persona-rag-integration-design.md 참고)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import ReplyStyle, Review, Store, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_SENSITIVE_TONE_OVERRIDE = (
    "위생/안전 문제이거나 별점과 내용이 어긋나는 민감한 리뷰입니다. "
    "페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."
)

CATEGORY_LABELS = {
    "food_quality": "음식 품질(맛/온도/양)",
    "delivery": "배달(지연/파손)",
    "hygiene": "위생/이물질",
    "service": "응대",
    "price": "가격",
    "missing_or_wrong_item": "오배송/누락",
}


def _build_system_prompt(store: Store, style_rules: str, examples, tone_instruction: str) -> str:
    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{ex.reply_text}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    return f"""너는 "{store.name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[답글 톤]
{tone_instruction}

[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은
반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""


def _build_user_message(review: Review, category_label: str, repeat_count: int) -> str:
    lines = [f"별점: {review.rating}"]
    if review.category == "no_issue":
        # "불만 유형: no_issue"라고 그대로 넣으면 모델이 없는 불만을 억지로
        # 찾아 사과하게 될 수 있다 — 칭찬/무난 리뷰는 불만 프레이밍 자체를
        # 빼고, 리뷰에 실제로 담긴 요청·취향(예: "더 매웠으면")이 있으면
        # 그것만 자연스럽게 반영하도록 안내한다.
        lines.append("특이 불만 없음(칭찬 또는 중립적인 리뷰). 리뷰에 구체적인 취향/요청이 담겨있으면 자연스럽게 반영하고, 없으면 감사 인사 위주로 답하세요.")
    else:
        lines.append(f"불만 유형: {category_label}")
    lines.append(f'내용: "{review.content}"')
    lines.append(f"이 고객의 누적 주문 횟수: {review.customer_order_count}회")
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if review.category != "no_issue" and repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)


def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES

    examples = fetch_golden_examples(db, store.id, review.category, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = CATEGORY_LABELS.get(review.category, review.category)

    tone_instruction = (
        _SENSITIVE_TONE_OVERRIDE
        if review.is_sensitive or review.sentiment_conflict
        else style.tone_instruction
    )

    system_prompt = _build_system_prompt(store, style_rules, examples, tone_instruction)
    user_message = _build_user_message(review, category_label, repeat_count)
    return client.call_sonnet(system_prompt, user_message, max_tokens=800)
