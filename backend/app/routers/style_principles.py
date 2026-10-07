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


def _row(p: StoreStyleProfile) -> dict:
    return {
        "category": p.category, "label": _label(p.category), "rules": p.rules,
        "generated_from_count": p.generated_from_count, "needs_confirmation": p.needs_confirmation,
        "updated_at": p.updated_at.isoformat(),
    }


def _owned_store(sid: int, user: User, db: Session) -> Store:
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")
    return store


@router.get("/style-principles")
def list_principles(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    _owned_store(sid, user, db)

    rows = db.scalars(
        select(StoreStyleProfile)
        .where(StoreStyleProfile.store_id == sid, StoreStyleProfile.needs_confirmation.is_(True))
        .order_by(StoreStyleProfile.updated_at)
    ).all()
    return {"principles": [_row(p) for p in rows]}


@router.get("/style-principles/all")
def list_all_principles(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """확인 대기(needs_confirmation=true) 여부와 무관하게 이 매장에 지금
    존재하는 원칙 전부를 보여준다 — "AI 원칙 확인" 카드가 확인 대기인
    것만 보여주고 나면 다시 열어볼 길이 없다는 사장님 피드백(2026-10-07
    실사용 중 발견)으로 추가했다. 카테고리 알파벳 순 정렬(표시는
    카테고리 라벨 기준이 아니라 원본 category 값 기준 — 화면에서
    프론트가 보기 좋은 순서로 다시 정렬해도 된다)."""
    sid = store_id or get_user_default_store_id(user, db)
    _owned_store(sid, user, db)

    rows = db.scalars(
        select(StoreStyleProfile)
        .where(StoreStyleProfile.store_id == sid)
        .order_by(StoreStyleProfile.category)
    ).all()
    return {"principles": [_row(p) for p in rows]}


class ConfirmPrincipleRequest(BaseModel):
    rules: str


@router.post("/style-principles/{category}/confirm")
def confirm_principle(
    category: str, body: ConfirmPrincipleRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    _owned_store(sid, user, db)

    profile = db.get(StoreStyleProfile, (sid, category))
    if profile is None:
        raise HTTPException(404, "해당 카테고리의 원칙이 없습니다")

    # 이미 확인된 원칙이어도(needs_confirmation=false) 그대로 수정할 수
    # 있다 — "확인 대기" 상태인지는 이 엔드포인트의 전제조건이 아니다.
    # GET /style-principles/all이 이미 확인된 것도 보여주고, 사장님이
    # 언제든 다시 눌러 고칠 수 있게 하는 게 바로 이 성질에 기대고 있다.
    profile.rules = body.rules
    profile.needs_confirmation = False
    db.commit()
    return _row(profile)
