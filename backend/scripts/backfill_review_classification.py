"""리뷰 분류(category/is_sensitive/sentiment_conflict) 기능이 2026-08-21에
처음 배포되면서, 그 이전에 이미 동기화된 리뷰는 한 번도 분류되지 않고
스키마 기본값(no_issue/False/False)에 그대로 남아있었다(2026-08-24 실측
확인 — 예: "닭이 유독 딱딱하고 잡내가 나서..." 같은 명백한 1점 불만
리뷰가 no_issue로 방치됨). classify_review 실패 시에도 review_sync.py가
조용히 기본값으로 남기기 때문에(분류 API 장애가 동기화 자체를 막으면
안 되므로), "언제 동기화됐는지"만으로는 재분류가 필요한 리뷰를 정확히
가려낼 수 없다 — 그래서 이 스크립트는 대상 매장의 모든 리뷰를 무조건
재분류한다.

여러 번 실행해도 데이터가 깨지지 않는다(최신 분류 결과로 덮어쓸 뿐,
중복 삽입 없음) — 다만 매번 호출 비용은 다시 든다.

이미 답글이 달려(status='answered') 종결된 리뷰가 재분류로 새로
is_sensitive=true가 되어도 alerts는 만들지 않는다 — 이미 끝난 리뷰에
대해 지금 와서 "민감 리뷰 알림"이 뜨면 사장님이 혼란스럽다. 아직
미답변(unanswered/pending)인 리뷰만 새로 sensitive로 판정되면
alerts에 추가한다(review_sync.py의 최초 동기화 시점과 같은 규칙)."""

import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.classify import ClassificationError, classify_review
from app.models import Alert, Review


def backfill_review_classification(db, store_id: int) -> dict:
    reviews = db.scalars(select(Review).where(Review.store_id == store_id)).all()

    reclassified = 0
    failed = 0
    newly_sensitive_alerted = 0

    for review in reviews:
        try:
            classification = classify_review(review.content, review.rating)
        except ClassificationError:
            failed += 1
            continue

        was_sensitive = review.is_sensitive
        review.category = classification.category
        review.is_sensitive = classification.is_sensitive
        review.sentiment_conflict = classification.sentiment_conflict
        reclassified += 1

        if classification.is_sensitive and not was_sensitive and review.status != "answered":
            db.add(Alert(
                store_id=store_id, alert_type="sensitive_review",
                message=f"민감한 리뷰가 감지됐습니다: {review.menu_summary} 관련 — 우선 확인이 필요합니다",
                created_at=datetime.now(timezone.utc),
            ))
            newly_sensitive_alerted += 1

    db.commit()
    return {"reclassified": reclassified, "failed": failed, "newly_sensitive_alerted": newly_sensitive_alerted}


if __name__ == "__main__":
    store_id = int(sys.argv[1])
    session = SessionLocal()
    try:
        result = backfill_review_classification(session, store_id)
        print(
            f"재분류: {result['reclassified']}건, 분류 실패: {result['failed']}건, "
            f"신규 민감 알림: {result['newly_sensitive_alerted']}건"
        )
    finally:
        session.close()
