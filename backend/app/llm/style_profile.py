"""매장별 답글 스타일 규칙 캐싱 — golden_examples 중 is_manual=true AND
is_synthetic=false인 데이터로만 재생성한다. 가상 데이터로 스타일을
뽑으면 AI가 자기 산출물을 학습하는 순환 오염이 생기므로 이 필터는
반드시 지킨다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.models import GoldenExample, StoreStyleProfile

_SYSTEM_PROMPT = """너는 배달 음식점 사장님의 답글 스타일을 분석한다.
아래는 이 사장님이 실제로 쓴 답글 예시들이다. 이 사장님만의 말투, 태도,
구조적 특징(예: 원인 설명 방식, 사과 표현, 재방문 유도 방식)을 5~7줄의
규칙으로 요약하라. 다른 매장에도 그대로 적용될 법한 일반적인 조언이
아니라, 이 예시들에서 실제로 관찰되는 구체적 특징만 적어라. 규칙
목록만 출력하고 다른 설명은 붙이지 마라."""


def refresh_store_style_profile(db: Session, store_id: int) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
    ).all()
    if not examples:
        return

    user_message = "\n\n".join(
        f'리뷰: "{ex.review_text}"\n답글: "{ex.reply_text}"' for ex in examples
    )
    rules = client.call_sonnet(_SYSTEM_PROMPT, user_message, max_tokens=500)

    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store_id))
    if profile is None:
        db.add(StoreStyleProfile(
            store_id=store_id, rules=rules, generated_from_count=len(examples),
            updated_at=datetime.now(timezone.utc),
        ))
    else:
        profile.rules = rules
        profile.generated_from_count = len(examples)
        profile.updated_at = datetime.now(timezone.utc)
    db.commit()
