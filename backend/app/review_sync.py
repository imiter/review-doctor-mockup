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
from app.models import BaeminShopBrand, Review, ReviewSyncJob, StorePlatformConnection
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, fetch_all_reviews, map_review


def _upsert_shop_brand(db: Session, connection_id: int, shop_no: int, shop_name: str) -> None:
    """로그인 시 발견된 브랜드(shop_no/shop_name)를 upsert한다. 리뷰 동기화
    성공 여부와 무관하게 매장 발견 자체는 로그인 단계에서 이미 성공했으므로,
    리뷰 조회가 실패한 매장이라도 이름/번호는 저장해 프런트 브랜드 드롭다운에
    쓸 수 있게 한다."""
    existing = db.scalar(
        select(BaeminShopBrand).where(
            BaeminShopBrand.connection_id == connection_id,
            BaeminShopBrand.shop_no == str(shop_no),
        )
    )
    if existing is None:
        db.add(BaeminShopBrand(connection_id=connection_id, shop_no=str(shop_no), shop_name=shop_name))
    else:
        existing.shop_name = shop_name


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

    existing_ids = set(db.scalars(
        select(Review.external_review_id).where(Review.external_review_id.isnot(None))
    ).all())

    # 배민 리뷰 id(external_review_id)는 매장(브랜드)이 아니라 계정 전체에서
    # 유일하므로, 중복 판별 집합은 매장 루프 전체에 걸쳐 하나만 공유한다.
    total_fetched = 0
    total_inserted = 0
    succeeded_any = False
    last_error: str | None = None

    try:
        for shop_no, shop_name in session.shops:
            # 매장 발견 자체는 로그인 단계에서 이미 끝났으므로, 이후 리뷰
            # 조회가 이 매장에서 실패해도 브랜드 이름은 저장해둔다.
            _upsert_shop_brand(db, conn.id, shop_no, shop_name)

            try:
                raw_reviews = fetch_all_reviews(session.page, shop_no)
                mapped = [
                    map_review(
                        raw, store_id=job.store_id, platform_id=job.platform_id,
                        platform_shop_no=str(shop_no),
                    )
                    for raw in raw_reviews
                    if raw.get("displayStatus", "DISPLAY") == "DISPLAY"
                ]
            except (BaeminScrapeError, KeyError) as e:
                # 한 매장의 실패가 다른 매장 동기화를 막지 않는다 — 마지막
                # 에러 메시지만 기억해뒀다가, 모든 매장이 실패했을 때만 job
                # 실패 사유로 쓴다.
                last_error = f"리뷰 조회/매핑 실패 (매장: {shop_name}): {e}"
                continue

            succeeded_any = True
            total_fetched += len(mapped)
            for m in mapped:
                if m["external_review_id"] in existing_ids:
                    continue
                db.add(Review(**m))
                existing_ids.add(m["external_review_id"])
                total_inserted += 1
    finally:
        session.close()

    if not succeeded_any:
        # session.shops가 비어 있던 경우(사실상 불가능하지만 방어적으로)에도
        # last_error가 없을 수 있으므로 기본 메시지를 둔다.
        job.status = "failed"
        job.error_message = last_error or "동기화할 매장을 찾지 못했습니다"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    # 일부 매장만 실패한 경우는 job 실패로 보지 않는다 — 예: 4개 브랜드 중
    # 3개가 정상 동기화되고 1개만 일시적 오류라면 이는 대체로 성공한
    # 동기화이지 전체 실패가 아니다.
    job.status = "success"
    job.reviews_fetched = total_fetched
    job.reviews_inserted = total_inserted
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
