"""매장×카테고리별 답글 스타일 원칙 캐싱 — golden_examples 중
is_manual=true AND is_synthetic=false AND needs_confirmation=false인
데이터로만, 그 카테고리 안에서만 재생성한다. 가상 데이터로 스타일을
뽑으면 AI가 자기 산출물을 학습하는 순환 오염이 생기므로 이 필터는
반드시 지킨다. needs_confirmation=true 행(경로 C — 배민에 직접 단
답글이 기존 신뢰 예시 클러스터와 말투가 어긋나는 이상치로 판정된 것,
app/llm/rag.py의 promote_direct_reply_to_golden_example 참고)도 같은
이유로 제외한다 — 아직 사람이 확인하지 않은, 진짜 사장님 말투인지
의심되는 답글을 "이 사장님의 말투"를 정의하는 요약에 그대로 반영하면
안 된다(2026-10-06).

카테고리별 분리(2026-10-07, DeepTwin 원칙 UI 플랜, 스펙 4.1절)와 함께
StoreStyleProfile.needs_confirmation(이 테이블 고유 플래그) 로직도
여기서 관리한다 — 재생성 결과가 이전 rules 텍스트와 실제로 다를 때만
선다. "원칙이 실제로 바뀔 때만 뜬다"는 스펙 요구사항을 만족하려면 매
저장마다 무조건 플래그를 세우면 안 되고, 텍스트가 우연히 똑같이
재생성된 경우(또는 사장님이 이미 확인한 뒤 또 같은 텍스트가 나온 경우)는
플래그를 건드리지 않아야 한다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.llm import client
from app.models import GoldenExample, StoreStyleProfile

_SYSTEM_PROMPT = """너는 배달 음식점 사장님의 답글 스타일을 분석한다.
아래는 이 사장님이 실제로 쓴 답글 예시들이다. 이 사장님만의 말투, 태도,
구조적 특징(예: 원인 설명 방식, 사과 표현, 재방문 유도 방식)을 5~7줄의
규칙으로 요약하라. 다른 매장에도 그대로 적용될 법한 일반적인 조언이
아니라, 이 예시들에서 실제로 관찰되는 구체적 특징만 적어라. 규칙
목록만 출력하고 다른 설명은 붙이지 마라."""


def refresh_store_style_profile(db: Session, store_id: int, category: str) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
            GoldenExample.needs_confirmation.is_(False),
        )
    ).all()
    if not examples:
        return

    user_message = "\n\n".join(
        f'리뷰: "{ex.review_text}"\n답글: "{ex.reply_text}"' for ex in examples
    )
    rules = client.call_sonnet(_SYSTEM_PROMPT, user_message, max_tokens=500)

    profile = db.get(StoreStyleProfile, (store_id, category))
    changed = profile is None or profile.rules != rules
    if profile is None:
        db.add(StoreStyleProfile(
            store_id=store_id, category=category, rules=rules, generated_from_count=len(examples),
            needs_confirmation=changed, updated_at=datetime.now(timezone.utc),
        ))
    else:
        profile.rules = rules
        profile.generated_from_count = len(examples)
        profile.updated_at = datetime.now(timezone.utc)
        if changed:
            profile.needs_confirmation = True
    db.commit()


def refresh_store_style_profile_background(store_id: int, category: str) -> None:
    """FastAPI BackgroundTasks가 호출하는 얇은 래퍼 — 요청이 끝나면 요청
    스코프 세션(app.routers.reviews의 db)은 이미 닫혀 있을 수 있으므로,
    review_sync.py의 run_review_sync_job과 같은 이유로 자체 SessionLocal을
    연다."""
    db = SessionLocal()
    try:
        refresh_store_style_profile(db, store_id, category)
    finally:
        db.close()
