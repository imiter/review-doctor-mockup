"""리뷰 동기화 백그라운드 작업 오케스트레이션 — 스크래핑 + 매핑 + DB 적재.

`sync_reviews_for_job`는 순수하게 주어진 DB 세션으로만 동작해 테스트가 쉽다.
`run_review_sync_job`는 FastAPI BackgroundTasks가 실제로 호출하는 얇은 래퍼로,
요청과 독립적인 자기 세션(SessionLocal)을 연다 — 요청이 끝나면 요청 스코프
세션은 이미 닫혀 있기 때문이다.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_crypto import CredentialCryptoError, decrypt_credential
from app.db import SessionLocal
from app.models import Review, ReviewSyncJob, StorePlatformConnection
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review


def sync_reviews_for_job(job: ReviewSyncJob, conn: StorePlatformConnection, db: Session) -> None:
    """작업 상태를 반드시 종결(success/failed)시키는 바깥쪽 안전망.

    어떤 예외가 어디서 나든 — 로그인 단계든 스크래핑/매핑 단계든, 우리가
    미리 알고 있는 예외 타입이든 아니든 — 이 함수를 빠져나갈 때 job은 항상
    "running"이 아닌 상태로 끝난다. `_run_sync`가 이미 처리한 알려진 실패는
    거기서 더 구체적인 메시지와 함께 status="failed"로 커밋되고 그대로
    반환되므로, 여기 except 블록은 `_run_sync` 자체가 예상치 못하게 실패한
    경우(신규/미분류 예외)를 잡는 마지막 방어선 역할만 한다.
    """
    job.status = "running"
    db.commit()

    try:
        _run_sync(job, conn, db)
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error_message = f"동기화 중 예기치 못한 오류가 발생했습니다: {e}"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()


def _run_sync(job: ReviewSyncJob, conn: StorePlatformConnection, db: Session) -> None:
    try:
        credential = decrypt_credential(conn.credential_ciphertext)
        session = baemin_login(credential["login_id"], credential["password"])
    except (BaeminLoginError, CredentialCryptoError) as e:
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
        existing_ids.add(m["external_review_id"])
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
