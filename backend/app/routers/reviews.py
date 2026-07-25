from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ReplyStyle, ReplyTemplate, Review, ReviewReply

router = APIRouter(prefix="/api", tags=["reviews"])


def band_of(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


class DraftRequest(BaseModel):
    style_id: int


class ReplyRequest(BaseModel):
    style_id: int
    content: str


@router.get("/reply-styles")
def list_styles(db: Session = Depends(get_db)):
    styles = db.scalars(select(ReplyStyle).order_by(ReplyStyle.id)).all()
    return [{"id": s.id, "name": s.name, "description": s.description} for s in styles]


@router.get("/reviews")
def list_reviews(
    status: str | None = None,
    store_platform_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Review).order_by(Review.created_at.desc())
    if status:
        stmt = stmt.where(Review.status == status)
    if store_platform_id:
        stmt = stmt.where(Review.store_platform_id == store_platform_id)
    reviews = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "store_name": r.store_platform.store.name,
            "platform_name": r.store_platform.platform.name,
            "rating": r.rating,
            "content": r.content,
            "reviewer_name": r.reviewer_name,
            "has_photo": r.has_photo,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "reply": {"content": r.reply.content, "style_id": r.reply.style_id} if r.reply else None,
        }
        for r in reviews
    ]


@router.post("/reviews/{review_id}/reply/draft")
def draft_reply(review_id: int, body: DraftRequest, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "리뷰 없음")
    template = db.scalar(
        select(ReplyTemplate).where(
            ReplyTemplate.style_id == body.style_id,
            ReplyTemplate.rating_band == band_of(review.rating),
        )
    )
    if template is None:
        raise HTTPException(404, "해당 스타일/별점대 템플릿 없음")
    content = template.template_text.replace("{reviewer_name}", review.reviewer_name)
    return {"content": content, "style_id": body.style_id}


@router.post("/reviews/{review_id}/reply")
def save_reply(review_id: int, body: ReplyRequest, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "리뷰 없음")
    if review.reply is not None:
        raise HTTPException(409, "이미 답글이 존재함")
    reply = ReviewReply(
        review_id=review.id, style_id=body.style_id,
        content=body.content, created_at=datetime.now(),
    )
    review.status = "answered"
    db.add(reply)
    db.commit()
    return {"id": reply.id, "content": reply.content}
