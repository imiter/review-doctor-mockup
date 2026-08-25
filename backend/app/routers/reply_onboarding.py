"""온보딩 데이터 부트스트랩 API. 배민 실계정 연결 직후의 빠른 마법사와
대시보드의 하루 단위 트리클이 같은 onboarding_scenarios를 공유한다(설계
문서 2026-08-21-llm-rag-reply-onboarding-design.md 참고)."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.onboarding import find_uncovered_categories, get_or_create_scenario
from app.llm.rag import compute_golden_example_embedding_background
from app.llm.style_profile import refresh_store_style_profile_background
from app.models import GoldenExample, OnboardingScenario, Store, User
from app.plan import kst_today

router = APIRouter(tags=["reply-onboarding"])

_DAILY_TRICKLE_LIMIT = 3


def _row(s: OnboardingScenario) -> dict:
    return {
        "id": s.id,
        "category": s.category,
        "virtual_review_text": s.virtual_review_text,
        "draft_text": s.draft_text,
        "status": s.status,
    }


def _get_owned_scenario(db: Session, scenario_id: int, user: User) -> OnboardingScenario:
    scenario = db.get(OnboardingScenario, scenario_id, options=[joinedload(OnboardingScenario.store)])
    if scenario is None or scenario.store.user_id != user.id:
        raise HTTPException(404, "시나리오를 찾을 수 없습니다")
    return scenario


@router.post("/reply-onboarding/wizard")
def run_wizard(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None:
        raise HTTPException(404, "매장을 찾을 수 없습니다")

    categories = find_uncovered_categories(db, sid)
    scenarios = [get_or_create_scenario(db, store, c) for c in categories]
    return [_row(s) for s in scenarios]


@router.get("/reply-onboarding/today")
def get_today(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None:
        raise HTTPException(404, "매장을 찾을 수 없습니다")

    # 서버는 Railway에서 UTC로 돌아가지만 "오늘"의 경계는 KST 자정이어야 한다
    # (app.plan 모듈 docstring 참고) — date.today()를 직접 쓰면 UTC 자정까지
    # 최대 9시간 오늘 몫이 지연 갱신된다.
    today = kst_today()
    already_shown = db.scalars(
        select(OnboardingScenario).where(
            OnboardingScenario.store_id == sid,
            OnboardingScenario.shown_on == today,
        ).order_by(OnboardingScenario.id)
    ).all()
    if already_shown:
        return [_row(s) for s in already_shown if s.status == "pending"]

    uncovered = find_uncovered_categories(db, sid)
    existing_by_category = {
        s.category: s for s in db.scalars(
            select(OnboardingScenario).where(
                OnboardingScenario.store_id == sid,
                OnboardingScenario.category.in_(uncovered),
            )
        ).all()
    }
    # 아직 한 번도 시나리오가 만들어진 적 없는 카테고리를 먼저 보여준다 —
    # 그렇지 않으면 find_uncovered_categories가 항상 같은 순서로 반환하는
    # 카테고리 목록의 앞쪽 3개만 매일 반복 노출되고, 뒤쪽 카테고리는 사장님이
    # 앞쪽을 스킵해도 영원히 트리클에 나오지 않는다.
    never_shown = [c for c in uncovered if c not in existing_by_category]
    # shown_on(트리클에 실제로 노출된 날)이 정렬 기준이어야 한다 — created_at(행이
    # 만들어진 시점)으로 정렬하면 POST /wizard가 6개 카테고리를 한 번에 만드는
    # 실제 배포 플로우에서 never_shown이 영구히 비고 VALID_CATEGORIES 순서만
    # 반복 재현된다(이 파일 상단 주석 참고). shown_on IS NULL(트리클에 한 번도
    # 안 나온 것)은 date.min으로 최우선 취급하고, 동률은 created_at으로 정한다.
    previously_shown = sorted(
        (c for c in uncovered if c in existing_by_category),
        key=lambda c: (existing_by_category[c].shown_on or date.min, existing_by_category[c].created_at),
    )
    categories = (never_shown + previously_shown)[:_DAILY_TRICKLE_LIMIT]

    scenarios = []
    for c in categories:
        scenario = get_or_create_scenario(db, store, c)
        scenario.shown_on = today
        scenarios.append(scenario)
    db.commit()
    return [_row(s) for s in scenarios]


class AnswerRequest(BaseModel):
    content: str


@router.post("/reply-onboarding/scenarios/{scenario_id}/answer")
def answer_scenario(
    scenario_id: int, body: AnswerRequest, background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    scenario = _get_owned_scenario(db, scenario_id, user)
    if scenario.status == "answered":
        raise HTTPException(409, "이미 답변한 시나리오입니다")

    # 온보딩은 사장님이 직접 검토·제출한 것이므로, save_final_reply(코어
    # 설계)의 초안-대조 승격 판정과 달리 diff 비교 없이 항상 승격한다.
    example = GoldenExample(
        store_id=scenario.store_id, category=scenario.category,
        review_text=scenario.virtual_review_text, reply_text=body.content,
        is_manual=True, is_synthetic=False, source="onboarding",
        created_at=datetime.now(timezone.utc),
    )
    db.add(example)
    scenario.status = "answered"
    db.commit()
    background_tasks.add_task(refresh_store_style_profile_background, scenario.store_id)
    background_tasks.add_task(compute_golden_example_embedding_background, example.id)
    return _row(scenario)


@router.post("/reply-onboarding/scenarios/{scenario_id}/skip")
def skip_scenario(
    scenario_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    scenario = _get_owned_scenario(db, scenario_id, user)
    if scenario.status == "answered":
        raise HTTPException(409, "이미 답변한 시나리오입니다")
    scenario.status = "skipped"
    db.commit()
    return _row(scenario)
