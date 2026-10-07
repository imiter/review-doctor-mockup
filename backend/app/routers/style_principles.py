"""매장×카테고리별 답글 스타일 원칙("원칙 확인") — AI가 golden_examples에서
뽑아낸 요약(StoreStyleProfile)이 실제로 바뀔 때마다 사장님이 확인/수정할
수 있는 비차단 UI의 API. 훈련카드(reply_onboarding.py)와는 별개 장치다 —
훈련카드는 판단사례를 새로 만들고, 여기는 이미 쌓인 사례의 요약을
검수한다. 새 원칙은 이 확인 여부와 무관하게 이미 적용 중이다
(app/llm/agent_graph.py의 retrieve_memory_node가 needs_confirmation을
보지 않고 그냥 읽는다) — 여기서 하는 일은 "검수했는지" 표시뿐이다."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.generate import CATEGORY_LABELS
from app.models import Store, StoreStyleProfile, User

router = APIRouter(tags=["style-principles"])

_NO_ISSUE_LABEL = "특이 불만 없음"


def _label(category: str) -> str:
    return {**CATEGORY_LABELS, "no_issue": _NO_ISSUE_LABEL}.get(category, category)


@router.get("/style-principles")
def list_principles(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    rows = db.scalars(
        select(StoreStyleProfile)
        .where(StoreStyleProfile.store_id == sid, StoreStyleProfile.needs_confirmation.is_(True))
        .order_by(StoreStyleProfile.updated_at)
    ).all()
    return {
        "principles": [
            {
                "category": p.category, "label": _label(p.category), "rules": p.rules,
                "generated_from_count": p.generated_from_count, "updated_at": p.updated_at.isoformat(),
            }
            for p in rows
        ],
    }


class ConfirmPrincipleRequest(BaseModel):
    rules: str


@router.post("/style-principles/{category}/confirm")
def confirm_principle(
    category: str, body: ConfirmPrincipleRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    profile = db.get(StoreStyleProfile, (sid, category))
    if profile is None:
        raise HTTPException(404, "해당 카테고리의 원칙이 없습니다")

    profile.rules = body.rules
    profile.needs_confirmation = False
    db.commit()
    return {
        "category": profile.category, "label": _label(profile.category), "rules": profile.rules,
        "generated_from_count": profile.generated_from_count, "updated_at": profile.updated_at.isoformat(),
    }
