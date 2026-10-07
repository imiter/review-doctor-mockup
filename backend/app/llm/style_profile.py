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
from app.llm.embedding import cosine_similarity, embed_documents
from app.models import GoldenExample, StoreStyleProfile

_SYSTEM_PROMPT = """너는 배달 음식점 사장님의 답글 스타일을 분석한다.
아래는 이 사장님이 실제로 쓴 답글 예시들이다. 이 사장님만의 말투, 태도,
구조적 특징(예: 원인 설명 방식, 사과 표현, 재방문 유도 방식)을 5~7줄의
규칙으로 요약하라. 다른 매장에도 그대로 적용될 법한 일반적인 조언이
아니라, 이 예시들에서 실제로 관찰되는 구체적 특징만 적어라. 규칙
목록만 출력하고 다른 설명은 붙이지 마라."""

# 실측으로 보정 전이라 보수적으로 높게 잡은 잠정값(이 프로젝트의 다른
# 유사도 임계값들 — agent_graph.py의 복붙 체크 0.8, rag.py의 말투
# 일관성 체크 0.5 — 과 같은 종류, 운영하면서 조정 대상). 두 원칙
# 텍스트가 같은 예시 집합을 요약한 "표현만 다른 같은 뜻"인지, 진짜
# 다른 내용인지를 가르는 데 쓴다.
_RULES_SIMILARITY_THRESHOLD = 0.95


def _rules_materially_changed(old_rules: str, new_rules: str) -> bool:
    """두 원칙 텍스트가 의미상 실질적으로 다른지 판단한다. client.call_sonnet은
    temperature 기본값(1.0)이라 완전히 같은 예시로 다시 요약해도 표현이
    매번 달라진다(의미는 같은데 글자가 다름) — 바이트 비교(!=)로는 이
    경우를 거의 매번 "바뀌었다"로 오판해서 needs_confirmation이 사실상
    매 재생성마다 서는 문제가 생긴다(2026-10-07 최종 리뷰에서 지적).
    Voyage 임베딩 코사인 유사도로 재서, 임계값 아래로 떨어질 때만
    "실질적으로 바뀌었다"고 본다. 임베딩 호출이 실패하면(Voyage 키
    미설정 등) 보수적으로 "바뀌었다"로 처리한다 — 이 신호를 놓쳐서
    사장님이 확인할 기회를 못 얻는 것보다, 가끔 불필요한 확인 요청이
    한 번 더 뜨는 쪽이 안전하다."""
    if old_rules == new_rules:
        return False
    try:
        old_vec, new_vec = embed_documents([old_rules, new_rules])
        similarity = cosine_similarity(old_vec, new_vec)
    except Exception:
        return True
    return similarity < _RULES_SIMILARITY_THRESHOLD


def refresh_store_style_profile(db: Session, store_id: int, category: str) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            # is_manual/is_synthetic는 현재 모든 생성 경로가 항상
            # True/False로 고정해서 넣기 때문에 사실상 항상 참이다 —
            # 실질적으로 거르는 조건은 needs_confirmation뿐이다(2026-10-07
            # 최종 리뷰에서 지적, app/llm/rag.py의 fetch_golden_examples가
            # source 기반 3단계로 재작성된 것과 같은 종류의 정리가 이
            # 쿼리에는 아직 반영 안 됨 — 별도 작업으로 남겨둠).
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
            # ↓ golden_examples 쪽 플래그(경로 C 이상치, 말투 일관성
            # 체크 미통과) — 바로 아래 StoreStyleProfile.needs_confirmation
            # (원칙 확인 UI 대상 표시)과 이름만 같고 의미는 완전히 다르다.
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
    changed = profile is None or _rules_materially_changed(profile.rules, rules)
    if profile is None:
        db.add(StoreStyleProfile(
            store_id=store_id, category=category, rules=rules, generated_from_count=len(examples),
            needs_confirmation=True, updated_at=datetime.now(timezone.utc),
        ))
    else:
        profile.rules = rules
        profile.generated_from_count = len(examples)
        profile.updated_at = datetime.now(timezone.utc)
        if changed:
            # ↑ 이 테이블 쪽 플래그(원칙 확인 UI 대상 표시) — 바로 위
            # golden_examples 쪽 needs_confirmation과 이름만 같고
            # 의미는 완전히 다르다.
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
