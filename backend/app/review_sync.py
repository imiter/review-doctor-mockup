"""리뷰 동기화 백그라운드 작업 오케스트레이션 — 스크래핑 + 매핑 + DB 적재.

`sync_reviews_for_job`는 순수하게 주어진 DB 세션으로만 동작해 테스트가 쉽다.
`run_review_sync_job`는 FastAPI BackgroundTasks가 실제로 호출하는 얇은 래퍼로,
요청과 독립적인 자기 세션(SessionLocal)을 연다 — 요청이 끝나면 요청 스코프
세션은 이미 닫혀 있기 때문이다.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_crypto import CredentialCryptoError, decrypt_credential
from app.db import SessionLocal
from app.models import (
    BaeminShopBrand,
    DailySettlement,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, extract_owner_reply, fetch_all_reviews, map_review
from scrapers.baemin_stats import (
    BaeminStatsScrapeError,
    compute_repurchase_rates,
    fetch_account_settlement,
    fetch_current_month_orders,
    fetch_shop_stats,
    map_deposits_by_date,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    recent_months,
)


def upsert_shop_brand(db: Session, connection_id: int, shop_no: int, shop_name: str) -> None:
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


def upsert_daily_settlement(
    db: Session, store_id: int, platform_id: int, settle_date: str,
    *, sales_amount: int | None = None, deposit_amount: int | None = None,
) -> None:
    """`(store_id, platform_id, settle_date)` 기준 upsert. sales_amount와
    deposit_amount는 각각 None이면 기존 값을 건드리지 않는다 — 매출 API는
    성공했는데 정산 API만 실패한 부분 성공 시나리오를 지원하기 위해서다.
    기존 Mock 시드 행이 있으면 갱신하고, 다른 플랫폼(요기요/쿠팡이츠) 행은
    이 함수가 절대 건드리지 않는다(platform_id로 이미 스코프됨)."""
    d = date.fromisoformat(settle_date)
    existing = db.scalar(
        select(DailySettlement).where(
            DailySettlement.store_id == store_id,
            DailySettlement.platform_id == platform_id,
            DailySettlement.settle_date == d,
        )
    )
    if existing is None:
        db.add(DailySettlement(
            store_id=store_id, platform_id=platform_id, settle_date=d,
            sales_amount=sales_amount or 0, deposit_amount=deposit_amount or 0,
        ))
        return
    if sales_amount is not None:
        existing.sales_amount = sales_amount
    if deposit_amount is not None:
        existing.deposit_amount = deposit_amount


def upsert_repurchase_metric(
    db: Session, store_id: int, platform_id: int, metric_date: str,
    new_orders: int, repeat_orders: int, rate_raw: float, rate_adjusted: float,
) -> None:
    """`(store_id, platform_id, metric_date)` 기준 upsert. Task 1의
    `compute_repurchase_rates` 반환값을 그대로 이 함수에 넘기는 용도다."""
    d = date.fromisoformat(metric_date)
    existing = db.scalar(
        select(RepurchaseMetric).where(
            RepurchaseMetric.store_id == store_id,
            RepurchaseMetric.platform_id == platform_id,
            RepurchaseMetric.metric_date == d,
        )
    )
    if existing is None:
        db.add(RepurchaseMetric(
            store_id=store_id, platform_id=platform_id, metric_date=d,
            new_orders=new_orders, repeat_orders=repeat_orders,
            rate_raw=rate_raw, rate_adjusted=rate_adjusted,
        ))
        return
    existing.new_orders = new_orders
    existing.repeat_orders = repeat_orders
    existing.rate_raw = rate_raw
    existing.rate_adjusted = rate_adjusted


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
    # 실패한 매장을 전부 모은다(마지막 것만이 아니라) — 일부만 실패해도 그
    # 사실이 눈에 보여야 하고, 전부 실패했을 때도 어느 매장이 왜 실패했는지
    # 전부 알 수 있어야 한다.
    failed_shops: list[str] = []

    try:
        for shop_no, shop_name in session.shops:
            # 매장 발견 자체는 로그인 단계에서 이미 끝났으므로, 이후 리뷰
            # 조회가 이 매장에서 실패해도 브랜드 이름은 저장해둔다.
            upsert_shop_brand(db, conn.id, shop_no, shop_name)

            try:
                raw_reviews = fetch_all_reviews(session.page, shop_no)
                # raw를 map_review 결과와 함께 들고 있는다 — 신규로 실제
                # INSERT하는 리뷰에 대해서만 extract_owner_reply()로 이미
                # 달린 사장님 답글을 review_replies에 같이 적재하기 위해서다
                # (raw 원본이 없으면 답글 내용을 다시 알아낼 방법이 없다).
                mapped_with_raw = [
                    (
                        raw,
                        map_review(
                            raw, store_id=job.store_id, platform_id=job.platform_id,
                            platform_shop_no=str(shop_no),
                        ),
                    )
                    for raw in raw_reviews
                    if raw.get("displayStatus", "DISPLAY") == "DISPLAY"
                ]
            except (BaeminScrapeError, KeyError) as e:
                # 한 매장의 실패가 다른 매장 동기화를 막지 않는다 — 모든
                # 실패를 기록해뒀다가, 전부 실패했을 때는 job 실패 사유로,
                # 일부만 실패했을 때는 성공한 job에 눈에 보이는 경고로 남긴다.
                failed_shops.append(f"{shop_name}: {e}")
                continue

            succeeded_any = True
            total_fetched += len(mapped_with_raw)
            for raw, m in mapped_with_raw:
                if m["external_review_id"] in existing_ids:
                    continue
                review = Review(**m)
                db.add(review)
                # review_replies가 review_id FK로 참조하려면 실제 id가
                # 필요하다 — autoflush=False(app.db.SessionLocal)라 명시적으로
                # flush해야 방금 만든 review의 id가 채워진다.
                db.flush()
                owner_reply = extract_owner_reply(raw)
                if owner_reply is not None:
                    reply_content, replied_at = owner_reply
                    db.add(ReviewReply(
                        review_id=review.id, reply_type="final", style_id=None,
                        content=reply_content, created_at=replied_at,
                    ))
                existing_ids.add(m["external_review_id"])
                total_inserted += 1

        # 리뷰 동기화 성공 여부와 무관하게 매출/재주문율/입금은 별도로
        # 시도한다 — 리뷰가 전부 실패해도(예: 매장 목록이 비정상) 매출은
        # 여전히 유효할 수 있고, 반대로 리뷰만 성공하고 이쪽이 실패해도
        # job 전체를 실패로 만들지 않는다(설계 문서 에러 처리 표).
        # stats_errors는 매장별 실패만 기록한다 — sales_responses/crm_responses가
        # 비어 있는지는 아래에서 각각 따로 확인하므로 여기서 별도로 재해석하지
        # 않는다(하나의 원인을 두 곳에서 서로 다르게 판단하면 불일치가 생긴다).
        stats_errors: list[str] = []
        months = recent_months(3)
        sales_responses: list[dict] = []
        crm_responses: list[dict] = []
        for shop_no, shop_name in session.shops:
            try:
                s, c = fetch_shop_stats(session.page, shop_no, months)
                sales_responses.extend(s)
                crm_responses.extend(c)
            except (BaeminStatsScrapeError, KeyError) as e:
                stats_errors.append(f"{shop_name}: {e}")

        if sales_responses:
            for settle_date, amount in map_sales_by_date(sales_responses).items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, sales_amount=amount,
                )

        # 가게통계의 월별 조회는 완료된 3개월만 준다(실측 확인 — 진행 중인 이번
        # 달은 그 목록에 아예 없음). 이번 달 진행분은 주문내역 화면에서 별도로
        # 보완한다 — 계정 전체를 한 번에 반환하므로 매장 루프 밖에서 한 번만
        # 호출한다(fetch_shop_stats처럼 매장별로 반복하지 않는다).
        try:
            current_month_orders = fetch_current_month_orders(session.page)
            for settle_date, amount in map_orders_to_daily_sales(current_month_orders).items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, sales_amount=amount,
                )
        except (BaeminStatsScrapeError, KeyError) as e:
            stats_errors.append(f"이번 달 매출(주문내역) 동기화 실패: {e}")

        if crm_responses:
            rates = compute_repurchase_rates(map_repurchase_by_date(crm_responses))
            for metric_date, r in rates.items():
                upsert_repurchase_metric(
                    db, job.store_id, job.platform_id, metric_date,
                    new_orders=r["new_orders"], repeat_orders=r["repeat_orders"],
                    rate_raw=r["rate_raw"], rate_adjusted=r["rate_adjusted"],
                )

        today = date.today()
        try:
            settlement_responses = fetch_account_settlement(
                session.page, (today - timedelta(days=90)).isoformat(), today.isoformat(),
            )
            for settle_date, amount in map_deposits_by_date(settlement_responses).items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, deposit_amount=amount,
                )
        except (BaeminStatsScrapeError, KeyError) as e:
            stats_errors.append(f"정산(입금) 동기화 실패: {e}")
    finally:
        session.close()

    if not succeeded_any:
        # session.shops가 비어 있던 경우(사실상 불가능하지만 방어적으로)에도
        # failed_shops가 비어 있을 수 있으므로 기본 메시지를 둔다.
        job.status = "failed"
        job.error_message = "; ".join(failed_shops) if failed_shops else "동기화할 매장을 찾지 못했습니다"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    # 일부 매장만 실패한 경우는 job 실패로 보지 않는다 — 예: 4개 브랜드 중
    # 3개가 정상 동기화되고 1개만 일시적 오류라면 이는 대체로 성공한
    # 동기화이지 전체 실패가 아니다. 다만 부분 실패 자체는 조용히 묻히면 안
    # 되므로, success 상태를 유지한 채 error_message에 요약을 남긴다 — 이
    # 실패가 계속 반복되는 상황(예: 4개 중 3개가 매번 실패)이 "항상 깨끗한
    # success"로 영원히 가려지는 걸 막기 위해서다. 실패가 하나도 없었던
    # 흔한 경우에는 error_message를 건드리지 않고 None으로 둔다.
    job.status = "success"
    job.reviews_fetched = total_fetched
    job.reviews_inserted = total_inserted
    messages = []
    if failed_shops:
        messages.append(
            f"{len(session.shops)}개 중 {len(failed_shops)}개 매장 리뷰 동기화 실패: {'; '.join(failed_shops)}"
        )
    if stats_errors:
        messages.append(f"매출/재주문율/입금 동기화 실패: {'; '.join(stats_errors)}")
    if messages:
        job.error_message = " / ".join(messages)
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
