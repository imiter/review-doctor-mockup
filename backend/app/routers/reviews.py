"""리뷰 관리 + 답글 스타일. 모든 리뷰(칭찬/무난 포함)가 실제 Claude API
기반 RAG 생성을 탄다 — 원래 category="no_issue" 리뷰는 템플릿 치환만
썼으나, 별점은 높아도 구체적 피드백이 담긴 리뷰에 리뷰 내용과 무관한
정형 문구가 붙는 문제가 확인돼(2026-08-24) 전부 RAG로 통합했다(사장님
학습 말투가 항상 반영되도록). 자세한 배경은 app/llm/generate.py 모듈
docstring 참고."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.llm.generate import generate_ai_reply
from app.llm.style_profile import refresh_store_style_profile_background
from app.models import GoldenExample, ReplyStyle, Review, ReviewReply, Subscription, User
from app.plan import effective_plan, replies_used_today

router = APIRouter(tags=["reviews"])


@router.get("/reply-styles")
def list_reply_styles(db: Session = Depends(get_db)):
    styles = db.scalars(select(ReplyStyle).order_by(ReplyStyle.id)).all()
    return [{"id": s.id, "name": s.name, "description": s.description} for s in styles]


@router.get("/reviews")
def list_reviews(
    status: str | None = None,
    store_id: int | None = None,
    platform_shop_no: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    stmt = (
        select(Review)
        .where(Review.store_id == sid)
        .options(joinedload(Review.platform), joinedload(Review.replies))
        .order_by(Review.created_at.desc())
    )
    if status:
        stmt = stmt.where(Review.status == status)
    if platform_shop_no:
        stmt = stmt.where(Review.platform_shop_no == platform_shop_no)
    if date_from:
        try:
            start = datetime.combine(date.fromisoformat(date_from), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "date_from 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        stmt = stmt.where(Review.created_at >= start)
    if date_to:
        try:
            # date_to는 그날 하루 전체를 포함해야 하므로(23:59:59까지),
            # 다음날 자정을 배타적 상한으로 쓴다.
            end = datetime.combine(date.fromisoformat(date_to), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(400, "date_to 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        stmt = stmt.where(Review.created_at < end)

    reviews = db.scalars(stmt).unique().all()
    result = []
    for r in reviews:
        final_reply = next((rp for rp in r.replies if rp.reply_type == "final"), None)
        draft_reply = next((rp for rp in r.replies if rp.reply_type == "ai_draft"), None)
        secondary_replies = [rp for rp in r.replies if rp.reply_type == "secondary"]
        result.append({
            "id": r.id,
            "order_id": r.order_id,
            "platform_name": r.platform.name,
            "platform_shop_no": r.platform_shop_no,
            "menu_summary": r.menu_summary,
            "rating": r.rating,
            "content": r.content,
            "customer_nickname": r.customer_nickname,
            "customer_order_count": r.customer_order_count,
            "image_urls": r.image_urls,
            "status": r.status,
            "category": r.category,
            "is_sensitive": r.is_sensitive,
            "created_at": r.created_at.isoformat(),
            "final_reply": {"content": final_reply.content, "style_id": final_reply.style_id} if final_reply else None,
            "draft_reply": {"content": draft_reply.content, "style_id": draft_reply.style_id} if draft_reply else None,
            "secondary_replies": [
                {"id": rp.id, "content": rp.content, "created_at": rp.created_at.isoformat()}
                for rp in sorted(secondary_replies, key=lambda rp: rp.created_at)
            ],
        })
    return result


class GenerateReplyRequest(BaseModel):
    style_id: int


@router.post("/reviews/{review_id}/generate-reply")
def generate_reply(
    review_id: int, body: GenerateReplyRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")

    style = db.get(ReplyStyle, body.style_id)
    if style is None:
        raise HTTPException(404, "답글 스타일 없음")

    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if effective_plan(sub) == "basic":
        limit = sub.daily_reply_limit if sub else 10
        if replies_used_today(user, db) >= limit:
            raise HTTPException(
                403,
                detail={"message": "오늘 답글 생성 한도를 모두 사용했어요. Pro는 무제한이에요.", "error_code": "reply_limit_exceeded"},
            )

    try:
        content = generate_ai_reply(db, review, review.store, style)
    except Exception:
        raise HTTPException(
            503,
            detail={"message": "AI 답글 생성에 실패했어요. 잠시 후 다시 시도해주세요.", "error_code": "ai_generation_failed"},
        )
    tone_overridden = review.is_sensitive or review.sentiment_conflict

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id, "tone_overridden": tone_overridden}


class SaveReplyRequest(BaseModel):
    style_id: int | None = None
    content: str


@router.post("/reviews/{review_id}/reply")
def save_final_reply(
    review_id: int, body: SaveReplyRequest, background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")
    if review.status == "answered":
        raise HTTPException(409, "이미 답글이 등록된 리뷰입니다")

    reply = ReviewReply(
        review_id=review.id, reply_type="final", style_id=body.style_id,
        content=body.content, created_at=datetime.now(timezone.utc),
    )
    review.status = "answered"
    db.add(reply)
    db.flush()

    # category와 무관하게(no_issue 포함) 골든 예시로 승격한다 — 모든 리뷰가
    # RAG를 타게 되면서 no_issue 카테고리도 few-shot 예시가 쌓여야 실제
    # 학습된 말투가 반영된다(2026-08-24, app/llm/generate.py 모듈 docstring 참고).
    draft = db.scalar(
        select(ReviewReply)
        .where(ReviewReply.review_id == review.id, ReviewReply.reply_type == "ai_draft")
        .order_by(ReviewReply.created_at.desc())
    )
    # 초안이 아예 없이(직접 작성) 저장했거나, 초안과 다르게 고쳐서
    # 저장했으면 "진짜 사장님 목소리"로 보고 골든 예시로 승격한다.
    # 초안을 그대로 복붙했으면(AI 산출물 그대로) 승격하지 않는다.
    if draft is None or draft.content != reply.content:
        db.add(GoldenExample(
            store_id=review.store_id, category=review.category,
            review_text=review.content, reply_text=reply.content,
            is_manual=True, is_synthetic=False, source="organic",
            source_review_id=review.id, source_reply_id=reply.id,
            created_at=datetime.now(timezone.utc),
        ))
        background_tasks.add_task(refresh_store_style_profile_background, review.store_id)

    db.commit()
    return {"id": reply.id, "content": reply.content}


class SecondaryReplyRequest(BaseModel):
    content: str


@router.post("/reviews/{review_id}/secondary-reply")
def add_secondary_reply(
    review_id: int, body: SecondaryReplyRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """답글 완료 리뷰에 덧붙이는 2차(추가) 답글. 고객이 리뷰를 수정했거나 추가 안내가 필요할 때 사용."""
    review = db.get(Review, review_id, options=[joinedload(Review.store)])
    if review is None or review.store.user_id != user.id:
        raise HTTPException(404, "리뷰 없음")
    if review.status != "answered":
        raise HTTPException(409, "1차 답글이 등록된 리뷰에만 2차 답글을 추가할 수 있습니다")

    reply = ReviewReply(
        review_id=review.id, reply_type="secondary", style_id=None,
        content=body.content, created_at=datetime.now(timezone.utc),
    )
    db.add(reply)
    db.commit()
    return {"id": reply.id, "content": reply.content}
