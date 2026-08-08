"""리뷰 관리 + 답글 스타일. 답글 생성은 템플릿 기반 Mock — 실제 AI 호출 없음."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import ReplyStyle, Review, ReviewReply, Store, User

router = APIRouter(tags=["reviews"])


@router.get("/reply-styles")
def list_reply_styles(db: Session = Depends(get_db)):
    styles = db.scalars(select(ReplyStyle).order_by(ReplyStyle.id)).all()
    return [{"id": s.id, "name": s.name, "description": s.description} for s in styles]


def _band(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


def _fill_template(template: str, review: Review, store: Store) -> str:
    return (
        template.replace("{nickname}", review.customer_nickname)
        .replace("{menu}", review.menu_summary)
        .replace("{store}", store.name)
    )


@router.get("/reviews")
def list_reviews(
    status: str | None = None,
    store_id: int | None = None,
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
            "menu_summary": r.menu_summary,
            "rating": r.rating,
            "content": r.content,
            "customer_nickname": r.customer_nickname,
            "customer_order_count": r.customer_order_count,
            "status": r.status,
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

    template = {"low": style.template_low, "mid": style.template_mid, "high": style.template_high}[_band(review.rating)]
    content = _fill_template(template, review, review.store)

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id}


class SaveReplyRequest(BaseModel):
    style_id: int
    content: str


@router.post("/reviews/{review_id}/reply")
def save_final_reply(
    review_id: int, body: SaveReplyRequest,
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
