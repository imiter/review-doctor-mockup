"""온보딩 데이터 부트스트랩 — golden_examples가 비어있는 카테고리마다 가상
리뷰 + 마중물 초안을 만들어 사장님에게 보여주고, 사장님이 직접 고친 답변을
진짜 golden_example로 승격시키는 흐름의 핵심 로직. AI가 진짜 데이터를
복제·증강하는 게 아니라, 사장님에게서 새 진짜 데이터를 능동적으로 받는
방식으로만 데이터를 늘린다(모델 붕괴 방지 원칙, 설계 문서
2026-08-21-llm-rag-reply-onboarding-design.md 참고)."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.classify import VALID_CATEGORIES
from app.llm.generate import CATEGORY_LABELS, generate_ai_reply
from app.models import GoldenExample, OnboardingScenario, ReplySetting, ReplyStyle, Review, Store

# 실제 별점이 아니라 프롬프트 컨텍스트용 플레이스홀더 — generate_ai_reply의
# 프롬프트가 별점을 참고하므로 대표값만 채운다. 정밀할 필요 없다.
_REPRESENTATIVE_RATING = {
    "food_quality": 2,
    "delivery": 1,
    "hygiene": 1,
    "service": 2,
    "missing_or_wrong_item": 1,
    "price": 3,
}

_VIRTUAL_REVIEW_PROMPT_TEMPLATE = """너는 배달 음식점에 실제로 달릴 법한
고객 불만 리뷰를 하나 만든다. 아래 불만 유형에 해당하는, 자연스러운
한국어 리뷰를 1~3문장으로 작성하라. 특정 가게 이름이나 메뉴는 언급하지
말고, 일반적인 상황으로 써라.

불만 유형: {category_label}

리뷰 본문만 출력하고 다른 설명은 붙이지 마라."""


def generate_virtual_review(category: str) -> str:
    label = CATEGORY_LABELS[category]
    return client.call_haiku(
        "너는 배달앱 리뷰 예시를 만드는 도구다.",
        _VIRTUAL_REVIEW_PROMPT_TEMPLATE.format(category_label=label),
        max_tokens=200,
    )


def _default_style(db: Session, store: Store) -> ReplyStyle | None:
    setting = db.scalar(select(ReplySetting).where(ReplySetting.store_id == store.id))
    if setting is not None and setting.style is not None:
        return setting.style
    return db.scalar(select(ReplyStyle).order_by(ReplyStyle.id))


def find_uncovered_categories(db: Session, store_id: int) -> list[str]:
    covered = set(db.scalars(
        select(GoldenExample.category).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        ).distinct()
    ).all())
    return [c for c in VALID_CATEGORIES if c != "no_issue" and c not in covered]


def get_or_create_scenario(db: Session, store: Store, category: str) -> OnboardingScenario:
    """카테고리 하나에 대해 기존 시나리오가 있으면(pending/skipped 상관없이)
    재사용하고, 없으면 새로 만든다. 호출자가 이미 find_uncovered_categories로
    real golden_example이 있는 카테고리를 걸러낸 뒤 부르므로 여기서는
    고려하지 않는다."""
    def _select_existing():
        return db.scalar(
            select(OnboardingScenario).where(
                OnboardingScenario.store_id == store.id,
                OnboardingScenario.category == category,
            )
        )

    existing = _select_existing()
    if existing is not None:
        return existing

    virtual_review_text = generate_virtual_review(category)
    # DB에 저장하지 않는 임시 Review — 진짜 reviews 테이블을 가상 데이터로
    # 오염시키면 안 된다. generate_ai_reply는 이 속성들만 읽는다(review.id는
    # 참조하지 않음).
    fake_review = Review(
        store_id=store.id, platform_id=0, menu_summary="",
        rating=_REPRESENTATIVE_RATING[category], content=virtual_review_text,
        customer_nickname="", customer_order_count=1, category=category,
        is_sensitive=(category == "hygiene"), created_at=datetime.now(timezone.utc),
    )
    draft_text = generate_ai_reply(db, fake_review, store, _default_style(db, store))

    scenario = OnboardingScenario(
        store_id=store.id, category=category,
        virtual_review_text=virtual_review_text, draft_text=draft_text,
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db.add(scenario)
    try:
        db.commit()
    except IntegrityError:
        # 체크-후-삽입 사이의 benign race — 동시 요청 두 개가 모두 위의
        # _select_existing()을 통과한 뒤 하나가 먼저 커밋하면, 나중 커밋은
        # UNIQUE (store_id, category) 위반으로 여기서 실패한다. 이미 LLM
        # 호출 비용을 치른 뒤이므로 500을 내지 않고, 롤백 후 방금 다른
        # 요청이 만든 행을 다시 조회해 그걸 반환한다.
        db.rollback()
        existing = _select_existing()
        if existing is None:
            raise
        return existing
    return scenario
