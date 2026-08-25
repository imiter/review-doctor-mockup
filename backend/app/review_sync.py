"""리뷰 동기화 백그라운드 작업 오케스트레이션 — 스크래핑 + 매핑 + DB 적재.

`sync_reviews_for_job`는 순수하게 주어진 DB 세션으로만 동작해 테스트가 쉽다.
`run_review_sync_job`는 FastAPI BackgroundTasks가 실제로 호출하는 얇은 래퍼로,
요청과 독립적인 자기 세션(SessionLocal)을 연다 — 요청이 끝나면 요청 스코프
세션은 이미 닫혀 있기 때문이다.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.credential_crypto import CredentialCryptoError, decrypt_credential
from app.db import SessionLocal
from app.models import (
    AdCampaign,
    Alert,
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    Order,
    RepurchaseMetric,
    ReplySetting,
    ReplyStyle,
    Review,
    ReviewReply,
    ReviewSyncJob,
    Store,
    StorePlatformConnection,
)
from app.llm.classify import ClassificationError, classify_review
from app.llm.generate import generate_ai_reply
from scrapers.baemin_ads import BaeminAdsScrapeError, fetch_brand_click_metrics, fetch_cpc_booking, map_click_metrics_by_date
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reply_submit import BaeminReplySubmitError, submit_reply
from scrapers.baemin_reviews import BaeminScrapeError, extract_owner_reply, fetch_all_reviews, map_review
from scrapers.baemin_stats import (
    ORDER_BACKFILL_PAGE_CLICKS,
    BaeminStatsScrapeError,
    compute_order_sync_range,
    compute_repurchase_rates,
    compute_settlement_sync_range,
    fetch_account_settlement,
    fetch_orders,
    fetch_settlement_breakdown_details,
    fetch_shop_stats,
    filter_months_needing_sync,
    map_deposits_by_date,
    map_order_rows,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    map_settlement_breakdown_by_date,
    parse_baemin_datetime,
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
    commission_amount: int | None = None, delivery_fee_amount: int | None = None,
    customer_discount_amount: int | None = None, ad_cost_amount: int | None = None,
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
            commission_amount=commission_amount, delivery_fee_amount=delivery_fee_amount,
            customer_discount_amount=customer_discount_amount, ad_cost_amount=ad_cost_amount,
        ))
        # autoflush=False(app.db.SessionLocal)라 flush 없이는 이 세션의 다음
        # select()가 방금 add()한 행을 못 본다 — 같은 날짜를 매출(주문내역)과
        # 입금(정산내역)이 각각 다른 호출로 건드리는 흔한 경우(오늘 날짜는
        # 항상 두 소스 모두의 대상), flush 없이는 두 번째 호출도 "없음"으로
        # 보고 중복 INSERT를 시도해 UniqueViolation이 난다.
        db.flush()
        return
    if sales_amount is not None:
        existing.sales_amount = sales_amount
    if deposit_amount is not None:
        existing.deposit_amount = deposit_amount
    if commission_amount is not None:
        existing.commission_amount = commission_amount
    if delivery_fee_amount is not None:
        existing.delivery_fee_amount = delivery_fee_amount
    if customer_discount_amount is not None:
        existing.customer_discount_amount = customer_discount_amount
    if ad_cost_amount is not None:
        existing.ad_cost_amount = ad_cost_amount


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
        # upsert_daily_settlement와 같은 이유(autoflush=False) — 이 함수는
        # 현재 단일 호출 지점(crm_responses 루프, metric_date로 이미 dedup됨)
        # 이라 관측된 충돌은 없지만, 같은 select-then-insert 패턴이라 구조적
        # 위험은 동일해 예방적으로 맞춘다.
        db.flush()
        return
    existing.new_orders = new_orders
    existing.repeat_orders = repeat_orders
    existing.rate_raw = rate_raw
    existing.rate_adjusted = rate_adjusted


def upsert_brand_ad_click_metric(
    db: Session, store_id: int, platform_id: int, shop_no: str, metric_date: str,
    *, ad_spend: int, impressions: int, clicks: int, ad_orders: int, ad_revenue: int,
) -> None:
    """`(store_id, platform_id, shop_no, metric_date)` 기준 upsert. 계정
    전체 합산인 `upsert_daily_settlement`와 달리 브랜드(shop_no)까지
    키에 포함한다 — 우리가게클릭은 애초에 브랜드 단위로만 조회되는
    화면이라 계정 전체로 합산할 이유가 없다(설계 문서 스코프 결정 참고)."""
    d = date.fromisoformat(metric_date)
    existing = db.scalar(
        select(BrandAdClickMetric).where(
            BrandAdClickMetric.store_id == store_id,
            BrandAdClickMetric.platform_id == platform_id,
            BrandAdClickMetric.shop_no == shop_no,
            BrandAdClickMetric.metric_date == d,
        )
    )
    if existing is None:
        db.add(BrandAdClickMetric(
            store_id=store_id, platform_id=platform_id, shop_no=shop_no, metric_date=d,
            ad_spend=ad_spend, impressions=impressions, clicks=clicks,
            ad_orders=ad_orders, ad_revenue=ad_revenue,
        ))
        # upsert_daily_settlement와 같은 이유(autoflush=False인
        # app.db.SessionLocal) — 같은 브랜드의 여러 달 응답을 한 세션 안에서
        # 연달아 upsert하므로 flush 없이는 두 번째 호출부터 select()가 방금
        # add()한 행을 못 봐서 중복 INSERT를 시도한다(오늘 매출/입금
        # upsert에서 실제로 겪은 UniqueViolation과 같은 버그 클래스).
        db.flush()
        return
    existing.ad_spend = ad_spend
    existing.impressions = impressions
    existing.clicks = clicks
    existing.ad_orders = ad_orders
    existing.ad_revenue = ad_revenue


def upsert_order(
    db: Session, store_id: int, platform_id: int,
    *, order_no: str, ordered_at: str, menu_summary: str, order_type: str, amount: int,
) -> None:
    """`order_no` 기준 upsert(`orders.order_no`는 매장과 무관하게 전역
    유일 — schema.sql의 `UNIQUE` 제약과 동일하게 `order_no`만으로 조회한다).
    증분 동기화(`compute_order_sync_range`)가 며칠씩 겹치는 기간을 다시
    조회할 수 있어 같은 `order_no`가 여러 번 들어올 수 있다 — 그때마다
    최신 값으로 덮어쓴다(주문 상태가 뒤늦게 바뀌는 경우를 반영하기 위해).

    `ordered_at`은 배민이 준 `orderDateTime`("2026-08-13T02:19:37") 그대로,
    **타임존 오프셋이 없는 한국 벽시계 시간**이다. `orders.ordered_at`은
    `TIMESTAMPTZ`이므로 naive datetime을 그대로 넣으면 Postgres가 세션
    타임존(배포 환경에서 사실상 UTC)으로 해석해 실제보다 9시간 늦은 순간으로
    저장한다 — 화면에는 19~02시(치킨 배달의 정상적인 저녁·심야 피크)가
    04~11시로 찍히고, 15시 이후 주문(실측 데이터의 91%)은 날짜까지 하루
    밀린다(2026-08-13 최종 리뷰 발견). 게다가 같은 동기화 안에서
    `map_orders_to_daily_sales`는 같은 `orderDateTime`의 앞 10글자를 한국
    날짜로 그대로 쓰기 때문에, 아무것도 안 하면 하나의 원본 필드가 두 개의
    서로 다른 타임존 가정으로 저장되는 모순이 생긴다. 그래서 DB 경계인
    여기서 `parse_baemin_datetime`으로 한국 타임존을 명시적으로 붙여 절대
    시각을 정확히 맞춘다(변환 규칙과 근거는 그 함수 docstring 참고)."""
    dt = parse_baemin_datetime(ordered_at)
    existing = db.scalar(select(Order).where(Order.order_no == order_no))
    if existing is None:
        db.add(Order(
            store_id=store_id, platform_id=platform_id, order_no=order_no,
            ordered_at=dt, menu_summary=menu_summary,
            order_type=order_type, amount=amount,
        ))
        # 다른 upsert_* 함수들과 같은 이유(autoflush=False인 app.db.SessionLocal)
        # — 증분 조회 범위 안에서 같은 order_no가 여러 번 들어올 수 있어
        # flush 없이는 두 번째부터 select()가 방금 add()한 행을 못 보고
        # 중복 INSERT를 시도해 UniqueViolation이 난다.
        db.flush()
        return
    existing.ordered_at = dt
    existing.menu_summary = menu_summary
    existing.order_type = order_type
    existing.amount = amount


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

    # 자동 답글: reply_settings.auto_reply_enabled가 켜져 있으면 새로 들어온
    # 리뷰에 실제로 배민 답글을 자동 제출한다(2026-08-25, 사용자 확인).
    # auto_reply_min_rating 설정값과 무관하게 지금은 별점 5점으로만
    # 하드코딩해서 제한한다 — AI 생성과 실제 배민 제출이 사람 검토 없이
    # 자동으로 나가는 첫 기능이라, 가장 안전한 순수 긍정 리뷰로만
    # 시작한다(설정 화면의 auto_reply_min_rating을 낮춰도 이 하한을
    # 넘지 못한다). 매장별로 한 번만 조회해 리뷰 루프 안에서 재사용한다.
    _AUTO_REPLY_MIN_RATING_FLOOR = 5
    reply_settings = db.scalar(select(ReplySetting).where(ReplySetting.store_id == job.store_id))
    auto_reply_style = None
    if reply_settings is not None and reply_settings.auto_reply_enabled:
        auto_reply_style = db.get(ReplyStyle, reply_settings.style_id)
    store = db.get(Store, job.store_id)
    # 자동 답글 실패는 stats_errors와 같은 종류의 "부분 실패"다 — 이 리뷰
    # 자체는 이미 정상 저장됐으므로 shop 전체를 실패로 보면 안 되고, 실패
    # 사실만 조용히 묻히지 않게 모아서 job.error_message에 남긴다.
    auto_reply_errors: list[str] = []

    # 배민 리뷰 id(external_review_id)는 매장(브랜드)이 아니라 계정 전체에서
    # 유일하므로, 중복 판별 집합은 매장 루프 전체에 걸쳐 하나만 공유한다.
    total_fetched = 0
    total_inserted = 0
    succeeded_any = False
    # 리뷰가 전부 실패해도(succeeded_any == False) 매출/재주문율/입금 중
    # 하나라도 성공해 실제로 DB에 커밋된 데이터가 있다면 job을 failed로
    # 끝내면 안 된다 — 아래 네 블록(매출/이번달 매출/재주문율/입금) 각각의
    # upsert가 끝날 때마다 이 플래그를 True로 세운다.
    stats_succeeded_any = False
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
                raw_reviews = fetch_all_reviews(session.page, shop_no, existing_ids=existing_ids)
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
                try:
                    classification = classify_review(review.content, review.rating)
                    review.category = classification.category
                    review.is_sensitive = classification.is_sensitive
                    review.sentiment_conflict = classification.sentiment_conflict
                except ClassificationError:
                    # 분류 실패해도 리뷰 저장 자체는 막지 않는다 — 컬럼
                    # 기본값(no_issue)으로 남기고 계속 진행한다. 리뷰
                    # 동기화가 AI 분류 가용성에 발목잡히면 안 된다.
                    pass
                db.add(review)
                # review_replies가 review_id FK로 참조하려면 실제 id가
                # 필요하다 — autoflush=False(app.db.SessionLocal)라 명시적으로
                # flush해야 방금 만든 review의 id가 채워진다.
                db.flush()
                if review.is_sensitive:
                    db.add(Alert(
                        store_id=job.store_id, alert_type="sensitive_review",
                        message=f"민감한 리뷰가 감지됐습니다: {review.menu_summary} 관련 — 우선 확인이 필요합니다",
                        created_at=datetime.now(timezone.utc),
                    ))
                if review.rating <= 2:
                    # seed.sql은 orders.id = reviews.order_id로 조인해 부정
                    # 리뷰 알림을 만들었는데, 실 배민 리뷰는 order_id가 항상
                    # NULL이라(리뷰 API와 주문 API를 연결할 공통 키 없음)
                    # 이 조인에 절대 걸리지 않는다 — 그래서 실 연동 이후로는
                    # 1~2점 리뷰가 들어와도 알림이 전혀 안 만들어지고
                    # 있었다(2026-08-25 실측 확인). 리뷰가 실제로 DB에
                    # 들어오는 이 시점에 직접 만들도록 고쳤다.
                    db.add(Alert(
                        store_id=job.store_id, alert_type="negative_review",
                        message=f'{review.rating}점 부정 리뷰가 등록되었습니다: "{review.content[:30]}"',
                        created_at=datetime.now(timezone.utc),
                    ))
                owner_reply = extract_owner_reply(raw)
                if owner_reply is not None:
                    reply_content, replied_at = owner_reply
                    db.add(ReviewReply(
                        review_id=review.id, reply_type="final", style_id=None,
                        content=reply_content, created_at=replied_at,
                    ))
                elif auto_reply_style is not None and review.rating >= _AUTO_REPLY_MIN_RATING_FLOOR:
                    try:
                        content = generate_ai_reply(db, review, store, auto_reply_style)
                        submit_reply(session.page, shop_no, review.external_review_id, content)
                        db.add(ReviewReply(
                            review_id=review.id, reply_type="final", style_id=auto_reply_style.id,
                            content=content, created_at=datetime.now(timezone.utc),
                        ))
                        review.status = "answered"
                        # golden_examples로 승격하지 않는다 — 사람이 한 번도
                        # 검토하지 않은 순수 AI 산출물이다. save_final_reply(사장님이
                        # 직접 등록 버튼을 누른 경로)와 달리 여기서 승격하면
                        # "AI 산출물을 AI가 다시 학습하는" 순환 오염이 된다
                        # (golden_examples.is_manual=true는 사람이 직접 쓰거나
                        # 승인한 것이라는 전제 — CLAUDE.md 참고).
                    except Exception as e:
                        # 자동 답글 실패가 리뷰 저장 자체를 되돌리지 않는다 —
                        # 리뷰는 이미 정상 동기화됐고, 다음에 사장님이 수동으로
                        # 답글을 달 수 있다(review.status는 unanswered로 남음).
                        auto_reply_errors.append(f"리뷰 {review.id}(별점 {review.rating}): {e}")
                existing_ids.add(m["external_review_id"])
                total_inserted += 1

        # 리뷰 동기화 성공 여부와 무관하게 매출/재주문율/입금은 별도로
        # 시도한다 — 리뷰가 전부 실패해도(예: 매장 목록이 비정상) 매출은
        # 여전히 유효할 수 있고, 반대로 리뷰만 성공하고 이쪽이 실패해도
        # job 전체를 실패로 만들지 않는다(설계 문서 에러 처리 표).
        # stats_errors는 매장별 실패(가게통계 루프)뿐 아니라 계정 단위
        # 실패(이번 달 매출/재주문율/정산)도 함께 담는다 — sales_responses/
        # crm_responses가 비어 있는지는 아래에서 각각 따로 확인하므로 여기서
        # 별도로 재해석하지 않는다(하나의 원인을 두 곳에서 서로 다르게
        # 판단하면 불일치가 생긴다).
        stats_errors: list[str] = []
        today = date.today()

        # ── 증분 조회 커서/범위를 어떤 쓰기보다도 먼저 한 번에 계산한다 ──
        # 매출·입금·정산상세 세 소스는 전부 같은 (store_id, platform_id)의
        # daily_settlements를 커서로 **읽고**, 동시에 같은 테이블에 **쓴다**.
        # 그래서 "A 소스 upsert → B 소스 커서 읽기" 순서가 섞이면 방금 쓴
        # 행이 B의 커서로 잡혀 백필 범위가 무너진다 — 2026-08-19 리뷰에서
        # 실제로 확인된 Critical 버그가 정확히 이 형태였다: 매출/이번달주문
        # upsert가 오늘 날짜 행을 `deposit_amount=0`으로 INSERT하고
        # `db.flush()`까지 하는 바람에, 최초 동기화인데도 입금 커서가
        # "오늘"이 돼 90일 백필이 며칠짜리로 붕괴했다. 세 범위를 여기서
        # 전부 확정해두면 이 순서 의존 자체가 사라진다(이후 블록 순서를
        # 어떻게 바꿔도 안전하다).
        #
        # "이 소스로 아직 채워진 적 없음" 판정에 `.isnot(None)`을 쓰면 안
        # 된다: `sales_amount`/`deposit_amount`는 schema.sql/models.py에서
        # `INT NOT NULL DEFAULT 0`이라 그 조건이 모든 행에 항상 참인
        # 항등식이다(진짜 nullable인 건 정산상세 4개 컬럼뿐). 대신 `> 0`으로
        # 판정한다 — 실제로 0원인 날/달을 "미동기화"로 오판할 수는 있지만
        # 그때의 대가는 "한 번 더 조회한다"뿐이라 안전한 쪽으로 틀린다
        # (새 컬럼/테이블 추가 없이 가능한 가장 안전한 선택).
        # 우가클(brand_ad_click_metrics)은 별개 테이블이고 브랜드별
        # session.shops가 필요해 로그인 이후에나 계산 가능하므로, 이 오염과
        # 무관하게 자기 블록 안에 그대로 둔다.
        months = recent_months(3)
        synced_sales_dates = db.scalars(
            select(DailySettlement.settle_date).where(
                DailySettlement.store_id == job.store_id,
                DailySettlement.platform_id == job.platform_id,
                DailySettlement.sales_amount > 0,
            )
        ).all()
        synced_sales_months = {d.strftime("%Y-%m") for d in synced_sales_dates}
        # `months[-2]`(가장 최근 완료된 달)는 이미 동기화됐어도 항상 다시
        # 조회한다. 이유 두 가지:
        # (1) 이번 달 매출은 주문내역 경로가 진행분으로 채우는데, 달이 바뀌면
        #     그 달엔 이미 행이 있어서 필터가 가게통계 재조회를 건너뛴다 —
        #     그러면 배민이 확정한 월별 수치를 영영 못 받고, 그 달 마지막
        #     동기화일 이후~말일 매출은 어느 경로로도 안 채워진다. 진행 중이던
        #     달이 "완료된 달"로 바뀌는 순간 여기서 한 번 확정치로 덮어쓴다.
        # (2) crmInfo(재주문율)는 날짜 소급이 안 되는 "최근 7일" 고정
        #     스냅샷이라 매 동기화마다 가게통계 화면을 최소 한 달은 열어야
        #     한다. `months[-1]`(이번 달)은 화면 구조상 선택 자체가 불가능해
        #     (`_select_month_dropdown`이 False를 반환 → 하드 에러) 반드시
        #     완료된 달이어야 한다. always_include가 이 보장을 필터 결과와
        #     무관하게 항상 성립시키므로, 예전의 "결과가 비면 [months[-2]]로
        #     대체"하던 특수 분기는 필요 없어져 제거했다 — 그 분기는 완료된
        #     두 달이 이미 있고 이번 달만 없는 흔한 경우에 `[이번 달]` 하나만
        #     남겨 (2)의 하드 에러를 그대로 맞는 구멍이 있었다.
        # 매번 조회하는 달 수는 여전히 최대 1~2개라 증분 조회의 성능 이득은
        # 그대로 유지된다.
        sales_months_to_fetch = filter_months_needing_sync(
            months, synced_sales_months, always_include={months[-2]},
        )

        latest_deposit_date = db.scalar(
            select(func.max(DailySettlement.settle_date)).where(
                DailySettlement.store_id == job.store_id,
                DailySettlement.platform_id == job.platform_id,
                DailySettlement.deposit_amount > 0,
            )
        )
        deposit_window_start, deposit_window_end = compute_settlement_sync_range(
            latest_deposit_date, today, backfill_days=90,
        )

        # 정산 상세 4개 컬럼은 진짜 nullable이라(요기요/쿠팡이츠 행과 백필
        # 범위 밖 배민 과거 날짜를 NULL로 남겨 "데이터 없음"과 "0원"을
        # 구분하는 게 설계 의도) 여기서는 `.isnot(None)`이 올바른 판정이다 —
        # 위 두 소스와 달리 항등식이 아니다.
        latest_breakdown_date = db.scalar(
            select(func.max(DailySettlement.settle_date)).where(
                DailySettlement.store_id == job.store_id,
                DailySettlement.platform_id == job.platform_id,
                DailySettlement.commission_amount.isnot(None),
            )
        )
        detail_window_start, detail_window_end = compute_settlement_sync_range(
            latest_breakdown_date, today, backfill_days=30,
        )
        # ── 여기부터 실제 fetch/upsert. 위에서 확정한 범위만 쓴다. ─────────

        sales_responses: list[dict] = []
        crm_responses: list[dict] = []
        # 매출은 매장별 응답을 계정 전체로 합산해 저장하므로(daily_settlements에
        # 브랜드 차원이 없다) "일부 매장만 성공"을 저장하면 안 된다 — 아래
        # 참고. 그래서 이번 회차에 한 매장이라도 실패했는지를 따로 기록한다.
        sales_fetch_failed = False
        for shop_no, shop_name in session.shops:
            try:
                s, c = fetch_shop_stats(session.page, shop_no, sales_months_to_fetch)
                sales_responses.extend(s)
                crm_responses.extend(c)
            except (BaeminStatsScrapeError, KeyError) as e:
                sales_fetch_failed = True
                stats_errors.append(f"{shop_name}: {e}")

        if sales_fetch_failed:
            # 브랜드 A는 성공하고 B만 실패했을 때 A만의 합계를 저장하면, 그
            # 달이 `filter_months_needing_sync` 입장에서 "동기화 완료"로
            # 굳어 다음 동기화가 통째로 건너뛴다 — B의 몫은 영구 누락되고,
            # daily_settlements엔 브랜드 차원이 없어 사후 판별조차 못 한다.
            # 그래서 완전 실패 vs 완전 성공만 인정한다("부분 수집을 성공으로
            # 취급하지 않는다" 원칙, CLAUDE.md). 재주문율(crm)은 계정 합산이
            # 아닌 날짜별 스냅샷이고 커서로 굳지도 않으므로 아래에서 독립적으로
            # 계속 진행한다 — 매출만 이 판단의 대상이다.
            stats_errors.append(
                "일부 매장의 가게통계 조회가 실패해 이번 회차 매출 저장을 건너뜁니다"
                " — 부분 합산을 저장하면 그 달이 동기화 완료로 굳어 실패한 매장 몫이 영구 누락됩니다"
            )
        elif sales_responses:
            try:
                for settle_date, amount in map_sales_by_date(sales_responses).items():
                    upsert_daily_settlement(
                        db, job.store_id, job.platform_id, settle_date, sales_amount=amount,
                    )
                stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"매출(가게통계) 동기화 실패: {e}")

        # 가게통계의 월별 조회는 완료된 3개월만 준다(실측 확인 — 진행 중인 이번
        # 달은 그 목록에 아예 없음). 이번 달 진행분은 주문내역 화면에서 별도로
        # 보완한다 — 계정 전체를 한 번에 반환하므로 매장 루프 밖에서 한 번만
        # 호출한다(fetch_shop_stats처럼 매장별로 반복하지 않는다).
        try:
            today_for_orders = date.today()
            current_month_orders = fetch_orders(
                session.page, today_for_orders.replace(day=1).isoformat(), today_for_orders.isoformat(),
            )
            current_month_sales = map_orders_to_daily_sales(current_month_orders)
            for settle_date, amount in current_month_sales.items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, sales_amount=amount,
                )
            # 조회 자체는 성공했지만 데이터가 없는 흔한 정상 케이스(예: 이번
            # 달 주문이 아직 없음)까지 "성공"으로 잡으면 안 된다 — 실제로
            # 커밋된 행이 있을 때만 stats_succeeded_any를 True로 세운다.
            if current_month_sales:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"이번 달 매출(주문내역) 동기화 실패: {e}")

        try:
            latest_order = db.scalar(
                select(func.max(Order.ordered_at)).where(
                    Order.store_id == job.store_id, Order.platform_id == job.platform_id,
                )
            )
            order_range_start, order_range_end = compute_order_sync_range(latest_order, date.today())
            # 이 호출만 페이지네이션 상한을 크게 올린다 — 최초 실행/Mock 정리
            # 직후의 3개월 백필은 실측(2026-08-13) 1,541건 = 약 155페이지라
            # 기본 상한으로는 구조적으로 도달할 수 없다. 위 "이번 달 매출
            # 보완" 호출은 한 달치라 기본값을 그대로 쓴다.
            order_contents = fetch_orders(
                session.page, order_range_start.isoformat(), order_range_end.isoformat(),
                max_page_clicks=ORDER_BACKFILL_PAGE_CLICKS,
            )
            order_rows = map_order_rows(order_contents)
            for row in order_rows:
                upsert_order(
                    db, job.store_id, job.platform_id,
                    order_no=row["order_no"], ordered_at=row["ordered_at"],
                    menu_summary=row["menu_summary"], order_type=row["order_type"], amount=row["amount"],
                )
            if order_rows:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"주문내역(개별 주문) 동기화 실패: {e}")

        if crm_responses:
            try:
                rates = compute_repurchase_rates(map_repurchase_by_date(crm_responses))
                for metric_date, r in rates.items():
                    upsert_repurchase_metric(
                        db, job.store_id, job.platform_id, metric_date,
                        new_orders=r["new_orders"], repeat_orders=r["repeat_orders"],
                        rate_raw=r["rate_raw"], rate_adjusted=r["rate_adjusted"],
                    )
                stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"재주문율 동기화 실패: {e}")

        try:
            settlement_responses = fetch_account_settlement(
                session.page, deposit_window_start.isoformat(), deposit_window_end.isoformat(),
            )
            # 배민 정산은 배치 지급 캘린더라 주말/공휴일 등 실제 배치가 없는
            # 날짜는 fetch 응답에 아예 등장하지 않는다. 그런 갭 날짜의 기존
            # daily_settlements 행을 그대로 두면 시드 때 넣어둔 Mock
            # deposit_amount가 영원히 남아 실데이터와 조용히 섞인다. 그래서
            # 실제 배치를 적용하기 전에, 이번에 조회를 시도한 날짜 범위
            # (store_id+platform_id로 엄격히 스코프, 다른 플랫폼/범위 밖
            # 날짜는 절대 건드리지 않음) 안의 기존 행부터 0으로 초기화한다 —
            # 갭 날짜는 결과적으로 0(입금 없음)으로 남고, 실제 배치가 있는
            # 날짜만 아래 루프가 다시 실제 금액으로 채운다. 이 리셋 범위도
            # window_start/window_end로 좁아진 증분 조회 범위를 그대로
            # 따라간다 — 조회 안 한 과거 날짜의 기존 값을 잘못 지우지 않는다.
            db.execute(
                update(DailySettlement)
                .where(
                    DailySettlement.store_id == job.store_id,
                    DailySettlement.platform_id == job.platform_id,
                    DailySettlement.settle_date >= deposit_window_start,
                    DailySettlement.settle_date <= deposit_window_end,
                )
                .values(deposit_amount=0)
            )
            daily_deposits = map_deposits_by_date(settlement_responses)
            for settle_date, amount in daily_deposits.items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, deposit_amount=amount,
                )
            if daily_deposits:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"정산(입금) 동기화 실패: {e}")

        # 위 deposit_amount 블록과 달리 이 블록은 시작 전에 기존 값을 0으로
        # 초기화하지 않는다 — deposit_amount는 배치 지급 캘린더의 갭 날짜를
        # Mock 시드 오염 없이 0으로 남기기 위해 일괄 리셋이 필요했지만, 이
        # 신규 컬럼 4개는 그런 갭 개념이 없고(부분 캡처 실패는 이미
        # `fetch_settlement_breakdown_details`의 하드 에러가 걸러준다), 여기서
        # 일괄 리셋을 하면 이번 동기화가 부분적으로만 성공했을 때 이전에
        # 이미 확보돼 있던 좋은 데이터까지 지워버리는 위험이 생긴다 —
        # 그래서 의도적으로 리셋 없이 upsert만 한다(2026-08-13, 최종 리뷰에서
        # 확인된 결정 — 실제 블랭킷 리셋 구현은 별도 설계 논의가 필요해
        # 범위 밖으로 남긴다).
        try:
            breakdown_details = fetch_settlement_breakdown_details(
                session.page, detail_window_start.isoformat(), detail_window_end.isoformat(),
            )
            breakdown_by_date = map_settlement_breakdown_by_date(breakdown_details)
            for settle_date, amounts in breakdown_by_date.items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date,
                    commission_amount=amounts["commission_amount"],
                    delivery_fee_amount=amounts["delivery_fee_amount"],
                    customer_discount_amount=amounts["customer_discount_amount"],
                    ad_cost_amount=amounts["ad_cost_amount"],
                )
            if breakdown_by_date:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"정산 상세(수수료/배달비/고객할인/우가클비용) 동기화 실패: {e}")

        # 우리가게클릭은 매출/입금/재주문율과 달리 계정 전체로 합산하지
        # 않는다 — 애초에 브랜드(shop_no) 단위로만 조회되는 화면이라
        # 브랜드별로 완전히 분리해서 저장한다(설계 문서 스코프 결정).
        # 그래서 fetch_shop_stats처럼 매장 루프 안에서 브랜드마다 upsert도
        # 그 자리에서 바로 한다 — 나중에 합치는 단계가 없다.
        current_month = date.today().strftime("%Y-%m")
        for shop_no, shop_name in session.shops:
            try:
                synced_click_dates = db.scalars(
                    select(BrandAdClickMetric.metric_date).where(
                        BrandAdClickMetric.store_id == job.store_id,
                        BrandAdClickMetric.platform_id == job.platform_id,
                        BrandAdClickMetric.shop_no == str(shop_no),
                    )
                ).all()
                synced_click_months = {d.strftime("%Y-%m") for d in synced_click_dates}
                click_months_to_fetch = filter_months_needing_sync(
                    months, synced_click_months, always_include={current_month},
                )
                click_responses = fetch_brand_click_metrics(session.page, shop_no, click_months_to_fetch)
                click_by_date = map_click_metrics_by_date(click_responses)
                for metric_date, m in click_by_date.items():
                    upsert_brand_ad_click_metric(
                        db, job.store_id, job.platform_id, str(shop_no), metric_date,
                        ad_spend=m["ad_spend"], impressions=m["impressions"], clicks=m["clicks"],
                        ad_orders=m["ad_orders"], ad_revenue=m["ad_revenue"],
                    )
                if click_by_date:
                    stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"{shop_name} 우리가게클릭 동기화 실패: {e}")

            # CPC 입찰가는 캠페인이 실제로 이 shop_no에 연결돼 있을 때만 갱신한다
            # (ad_campaigns에 아직 이 브랜드의 캠페인이 없으면 조용히 건너뛴다 —
            # Task 2로 4브랜드 캠페인이 이미 있는 게 보통이지만, 신규 브랜드가
            # 연결 직후(캠페인 미생성) 동기화되는 경우까지 방어한다).
            campaign = db.scalar(select(AdCampaign).where(AdCampaign.shop_no == str(shop_no)))
            if campaign is not None:
                try:
                    booking = fetch_cpc_booking(session.page, shop_no)
                    campaign.current_cpc = booking["bid"]
                    stats_succeeded_any = True
                except Exception as e:
                    stats_errors.append(f"{shop_name} CPC 입찰가 동기화 실패: {e}")
    finally:
        session.close()

    if not succeeded_any and not stats_succeeded_any:
        # 리뷰도 전부 실패하고 매출/재주문율/입금 중 실제로 커밋된 것도
        # 하나도 없을 때만 job을 failed로 끝낸다 — 리뷰는 전부 실패했더라도
        # 매출 등 다른 소스 중 하나라도 실제로 DB에 커밋됐다면(stats_succeeded_any)
        # "실패"로 보고하면 안 된다(운영자가 아무것도 저장 안 된 줄 오해함).
        # session.shops가 비어 있던 경우(사실상 불가능하지만 방어적으로)에도
        # failed_shops가 비어 있을 수 있으므로 기본 메시지를 둔다.
        job.status = "failed"
        job.error_message = "; ".join(failed_shops) if failed_shops else "동기화할 매장을 찾지 못했습니다"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    # 일부 매장만 실패한 경우(또는 리뷰는 전부 실패했지만 매출/재주문율/입금
    # 중 하나라도 성공한 경우)는 job 실패로 보지 않는다 — 예: 4개 브랜드 중
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
    if auto_reply_errors:
        messages.append(f"자동 답글 실패: {'; '.join(auto_reply_errors)}")
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
