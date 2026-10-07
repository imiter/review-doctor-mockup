"""관리자 LLMOps 대시보드 — 답글 생성 LangGraph 파이프라인의 노드 구성,
실제 실행 트레이스(LangSmith), 카테고리별 정확도(AI초안-사장님최종본
유사도, draft_feedback_scores)를 보여준다. require_admin으로 보호된다.
노드 구성도 자체는 고정된 구조라 프론트에서 하드코딩해 그린다 — 이
라우터는 "실제로 무슨 일이 있었는지"(실행 이력, 정확도)만 담당한다.
실사용 중 "노드 간에 어떤 데이터가 오가는지, 리뷰가 어떻게 생성되는지
추적하고 싶다"는 요청으로 추가(2026-10-07)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.llm import observability
from app.llm.generate import CATEGORY_LABELS
from app.models import DraftFeedbackScore, User

router = APIRouter(tags=["admin-llmops"])

_NO_ISSUE_LABEL = "특이 불만 없음"


def _label(category: str) -> str:
    return {**CATEGORY_LABELS, "no_issue": _NO_ISSUE_LABEL}.get(category, category)


@router.get("/admin/llmops/runs")
def list_runs(limit: int = 20, admin: User = Depends(require_admin)):
    return {"runs": observability.list_recent_runs(limit=limit)}


@router.get("/admin/llmops/runs/{trace_id}")
def run_detail(trace_id: str, admin: User = Depends(require_admin)):
    detail = observability.get_run_detail(trace_id)
    if detail is None:
        raise HTTPException(404, "트레이스를 찾을 수 없습니다(LangSmith 미설정이거나 존재하지 않는 trace_id)")
    return detail


@router.get("/admin/llmops/accuracy")
def accuracy_by_category(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """전체 매장 통합 집계 — 관리자 화면이라 특정 매장에 한정하지 않는다
    (다른 admin 엔드포인트들, 예: admin_list_stores도 전체 매장을 본다)."""
    rows = db.execute(
        select(
            DraftFeedbackScore.category,
            func.avg(DraftFeedbackScore.similarity_score),
            func.count(DraftFeedbackScore.id),
        )
        .group_by(DraftFeedbackScore.category)
        .order_by(func.count(DraftFeedbackScore.id).desc())
    ).all()
    return {
        "categories": [
            {
                "category": category, "label": _label(category),
                "avg_similarity": round(float(avg_score), 4), "sample_count": count,
            }
            for category, avg_score, count in rows
        ],
    }
