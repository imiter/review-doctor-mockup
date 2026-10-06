"""AI 초안과 사장님 최종본의 유사도를 기록한다(스펙 4.2절 "순환 측정
장치") — "보류→교정" 순환이 실제로 작동하는지(사장님 교정이 AI 초안에서
얼마나 벗어나는지) 숫자로 확인하는 장치다. 경로 A(reviews.py의
save_final_reply)와 경로 B(reply_onboarding.py의 answer_scenario) 둘 다
이 함수를 쓴다.

FastAPI BackgroundTasks가 호출하는 얇은 래퍼라 자체 SessionLocal을 연다
(app/llm/rag.py의 compute_golden_example_embedding_background와 동일한
패턴 — 요청 스코프 세션은 이미 닫혀 있을 수 있다).

Voyage 임베딩 계산과 LangSmith feedback 기록은 서로 독립적인 두 단계로
나눈다 — DB 기록(핵심, 대시보드가 읽는 자리)이 LangSmith API 상태에
발목잡히면 안 되기 때문이다. Voyage 호출 자체가 실패하면(키 미설정 등)
측정 전체를 스킵한다(점수가 없는데 억지로 0을 넣는 것보다 안전한 쪽).
LangSmith 쪽은 DB 기록이 끝난 뒤 별도로 시도하고 실패해도 이미 쓴 DB
행에는 영향 없다."""

from datetime import datetime, timezone

from app.db import SessionLocal
from app.llm.embedding import cosine_similarity, embed_documents
from app.models import DraftFeedbackScore

try:
    from langsmith import Client as _LangSmithClient
except ImportError:  # pragma: no cover — langsmith는 requirements.txt에 있어 항상 설치돼 있다
    _LangSmithClient = None


def record_draft_feedback_background(
    *, trace_id: str, draft_text: str, final_text: str, store_id: int, category: str,
    source_review_id: int | None = None, source_scenario_id: int | None = None,
) -> None:
    try:
        draft_vec, final_vec = embed_documents([draft_text, final_text])
        score = cosine_similarity(draft_vec, final_vec)
    except Exception:
        return  # Voyage 호출 실패(키 미설정 등) — 측정 자체를 스킵한다

    db = SessionLocal()
    try:
        db.add(DraftFeedbackScore(
            store_id=store_id, category=category, similarity_score=score,
            trace_id=trace_id, source_review_id=source_review_id,
            source_scenario_id=source_scenario_id,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    try:
        _LangSmithClient().create_feedback(run_id=trace_id, key="draft_final_similarity", score=score)
    except Exception:
        pass  # LangSmith 기록 실패해도 위 DB 기록은 이미 끝났다
