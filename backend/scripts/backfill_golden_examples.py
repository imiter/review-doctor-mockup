"""브레인스토밍 중 확인한, 사장님이 이미 실제로 작성한 별점 1~2점(부정)
리뷰 답글을 golden_examples로 백필한다. 이 스크립트가 대상으로 삼는
건 review_replies.reply_type='final'이 이미 있는 별점 1~2점 리뷰뿐이다
— 별점 3점 이상은 사장님이 직접 확인해주지 않아 "진짜 본인 목소리"인지
확신할 수 없으므로 이번 백필 대상에서 제외한다(설계 문서 2026-08-21
참고). 여러 번 실행해도 안전하다(같은 review_id는 중복 삽입하지 않음)."""

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.classify import ClassificationError, classify_review
from app.models import GoldenExample, Review, ReviewReply

_MAX_RATING = 2


def backfill_negative_review_replies(db, store_id: int) -> int:
    candidates = db.scalars(
        select(Review).where(Review.store_id == store_id, Review.rating <= _MAX_RATING)
    ).all()

    inserted = 0
    for review in candidates:
        already = db.scalar(
            select(GoldenExample).where(GoldenExample.source_review_id == review.id)
        )
        if already is not None:
            continue

        final_reply = db.scalar(
            select(ReviewReply).where(ReviewReply.review_id == review.id, ReviewReply.reply_type == "final")
        )
        if final_reply is None:
            continue

        try:
            classification = classify_review(review.content, review.rating)
            category = classification.category
        except ClassificationError:
            continue

        db.add(GoldenExample(
            store_id=store_id, category=category,
            review_text=review.content, reply_text=final_reply.content,
            is_manual=True, is_synthetic=False, source="backfill",
            source_review_id=review.id, source_reply_id=final_reply.id,
            created_at=final_reply.created_at,
        ))
        inserted += 1

    db.commit()
    return inserted


if __name__ == "__main__":
    store_id = int(sys.argv[1])
    session = SessionLocal()
    try:
        count = backfill_negative_review_replies(session, store_id)
        print(f"{count}건의 골든 예시를 백필했습니다.")
    finally:
        session.close()
