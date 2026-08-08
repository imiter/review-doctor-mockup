"""리뷰 동기화 백그라운드 작업 오케스트레이션 — 스크래핑 + 매핑 + DB 적재.

`sync_reviews_for_job`는 순수하게 주어진 DB 세션으로만 동작해 테스트가 쉽다.
`run_review_sync_job`는 FastAPI BackgroundTasks가 실제로 호출하는 얇은 래퍼로,
요청과 독립적인 자기 세션(SessionLocal)을 연다 — 요청이 끝나면 요청 스코프
세션은 이미 닫혀 있기 때문이다.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_crypto import decrypt_credential
from app.db import SessionLocal
from app.models import Review, ReviewSyncJob, StorePlatformConnection
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review


def sync_reviews_for_job(job: ReviewSyncJob, conn: StorePlatformConnection, db: Session) -> None:
    job.status = "running"
    db.commit()

    try:
        credential = decrypt_credential(conn.credential_ciphertext)
        session = baemin_login(credential["login_id"], credential["password"])
    except BaeminLoginError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    try:
        raw_reviews = fetch_all_reviews(session.page, session.shop_no)
        mapped = [
            map_review(raw, store_id=job.store_id, platform_id=job.platform_id)
            for raw in raw_reviews
            if raw.get("displayStatus", "DISPLAY") == "DISPLAY"
        ]
    except (BaeminScrapeError, KeyError) as e:
        job.status = "failed"
        job.error_message = f"리뷰 조회/매핑 실패: {e}"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return
    finally:
        session.close()

    existing_ids = set(db.scalars(
        select(Review.external_review_id).where(Review.external_review_id.isnot(None))
    ).all())

    inserted = 0
    for m in mapped:
        if m["external_review_id"] in existing_ids:
            continue
        db.add(Review(**m))
        inserted += 1

    job.status = "success"
    job.reviews_fetched = len(mapped)
    job.reviews_inserted = inserted
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def run_review_sync_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ReviewSyncJob, job_id)
        conn = db.scalar(
            select(StorePlatformConnection).where(
                StorePlatformConnection.store_id == job.store_id,
                StorePlatformConnection.platform_id == job.platform_id,
            )
        )
        sync_reviews_for_job(job, conn, db)
    finally:
        db.close()
