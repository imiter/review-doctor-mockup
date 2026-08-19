"""가게 연결. 매장×플랫폼 N:M 연결을 관리한다.

배민은 실제 사장님광장 계정으로 로그인해 매장을 연결하고 리뷰를 동기화할 수
있다. 그 외 플랫폼(쿠팡이츠, 요기요)은 여전히 Mock — 연결하면 Mock 스토어
아이디/사업자번호가 즉석에서 만들어질 뿐, 실제 계정 연동은 하지 않는다.
"""

import hmac
import os
import random
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_user_default_store_id
from app.credential_crypto import encrypt_credential
from app.db import get_db
from app.models import BaeminShopBrand, Platform, ReviewSyncJob, Store, StorePlatformConnection, User
from app.review_sync import run_review_sync_job, upsert_shop_brand
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login

router = APIRouter(tags=["store-connections"])

# Railway처럼 이 프로세스에서 배민 로그인을 직접 실행하면 클라우드 IP가 봇
# 탐지에 걸려 로그인 폼 자체가 안 뜬다(app/routers/ads.py의 apply-bid와 동일한
# 원인, 2026-08-18 실측 확인). CRAWL_WORKER_URL이 설정된 배포 환경에서는
# 로그인/동기화 전체를 홈 IP 워커(맥북)에 위임한다.
_CRAWL_WORKER_URL = os.getenv("CRAWL_WORKER_URL")
_CRAWL_WORKER_SECRET = os.getenv("CRAWL_WORKER_SECRET", "")


def _require_worker_secret(x_worker_secret: str) -> None:
    if not _CRAWL_WORKER_SECRET or not hmac.compare_digest(x_worker_secret, _CRAWL_WORKER_SECRET):
        raise HTTPException(403, "워커 비밀키가 일치하지 않습니다")


def _row(c: StorePlatformConnection) -> dict:
    return {
        "id": c.id,
        "platform_id": c.platform_id,
        "platform_code": c.platform.code,
        "platform_name": c.platform.name,
        "brand_color": c.platform.brand_color,
        "platform_store_id": c.platform_store_id,
        "business_number": c.business_number,
        "has_real_credential": c.credential_ciphertext is not None,
        "connected_at": c.connected_at.isoformat(),
    }


@router.get("/store-connections")
def list_connections(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    connections = db.scalars(
        select(StorePlatformConnection)
        .where(StorePlatformConnection.store_id == sid)
        .options(joinedload(StorePlatformConnection.platform))
        .order_by(StorePlatformConnection.connected_at)
    ).all()
    return [_row(c) for c in connections]


class ConnectRequest(BaseModel):
    platform_id: int
    store_id: int | None = None


@router.post("/store-connections", status_code=201)
def connect_platform(body: ConnectRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sid = body.store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    existing = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == body.platform_id
        )
    )
    if existing:
        raise HTTPException(409, "이미 연결된 플랫폼입니다")

    conn = StorePlatformConnection(
        store_id=sid, platform_id=body.platform_id,
        platform_store_id=f"MK-{random.randint(10_000_000, 99_999_999)}",  # Mock 스토어 아이디
        business_number=f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10000,99999)}",  # Mock
        connected_at=datetime.now(timezone.utc),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _row(conn)


@router.delete("/store-connections/{connection_id}", status_code=204)
def disconnect_platform(connection_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.get(StorePlatformConnection, connection_id, options=[joinedload(StorePlatformConnection.store)])
    if conn is None or conn.store.user_id != user.id:
        raise HTTPException(404, "연결 없음")
    db.delete(conn)
    db.commit()


class BaeminLoginRequest(BaseModel):
    platform_login_id: str
    platform_login_password: str


@router.post("/store-connections/baemin/login")
def baemin_login_endpoint(
    body: BaeminLoginRequest, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if platform is None:
        raise HTTPException(500, "배민 플랫폼이 등록되어 있지 않습니다")

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/baemin-login",
                json={"login_id": body.platform_login_id, "password": body.platform_login_password},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=60,
            )
        except httpx.RequestError as e:
            raise HTTPException(502, f"크롤 워커에 연결할 수 없습니다: {e}")
        if resp.status_code != 200:
            try:
                detail = resp.json()["detail"]
            except Exception:
                detail = resp.text[:500]
            raise HTTPException(400, detail)
        data = resp.json()
        shop_no = data["shop_no"]
        shop_name = data["shop_name"]
        shops = [(s["shop_no"], s["shop_name"]) for s in data["shops"]]
    else:
        try:
            session = baemin_login(body.platform_login_id, body.platform_login_password)
        except BaeminLoginError as e:
            raise HTTPException(400, str(e))
        shop_no, shop_name, shops = session.shop_no, session.shop_name, session.shops
        session.close()

    ciphertext = encrypt_credential(body.platform_login_id, body.platform_login_password)

    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None:
        conn = StorePlatformConnection(
            store_id=sid, platform_id=platform.id,
            platform_store_id=str(shop_no),
            connected_at=datetime.now(timezone.utc),
        )
        db.add(conn)
    else:
        conn.platform_store_id = str(shop_no)
    conn.credential_ciphertext = ciphertext
    db.flush()  # conn.id가 신규 연결이면 아직 없으므로 brand upsert 전에 확보해둔다

    # 로그인 시점에 이미 전체 브랜드 목록(session.shops)을 알고 있으므로 여기서
    # 바로 저장한다 — 첫 리뷰 동기화(몇 분 걸릴 수 있음)가 끝날 때까지
    # 기다리지 않아도 GET /store-connections/baemin/shops가 즉시 채워진다.
    for s_no, s_name in shops:
        upsert_shop_brand(db, conn.id, s_no, s_name)

    db.commit()
    db.refresh(conn)
    return {"connected": True, "shop_name": shop_name, "platform_store_id": conn.platform_store_id}


def _dispatch_sync_job(
    sid: int, platform: Platform, conn: StorePlatformConnection, db: Session,
    *, triggered_by: str,
    background_tasks: BackgroundTasks | None = None,
) -> ReviewSyncJob | None:
    """이미 진행 중인 잡이 있으면 None을 반환하고 아무 것도 하지 않는다.
    아니면 잡을 만들고 워커(설정돼 있으면) 또는 이 프로세스의 백그라운드
    작업으로 동기화를 시작시킨 뒤 그 잡을 반환한다.

    CRAWL_WORKER_URL도 없고 background_tasks도 None인 경로(스케줄러가
    워커 없는 로컬 개발 환경에서 도는 경우)에서는 이 함수가 잡을 만들기만
    하고 status="pending"인 채로 반환한다 — 실제로 동기화를 실행하는 건
    호출부 책임이다(app/scheduler.py의 run_scheduled_sync_cycle 참고)."""
    existing_job = db.scalar(
        select(ReviewSyncJob).where(
            ReviewSyncJob.store_id == sid,
            ReviewSyncJob.platform_id == platform.id,
            ReviewSyncJob.status.in_(("pending", "running")),
        )
    )
    if existing_job is not None:
        return None

    job = ReviewSyncJob(
        store_id=sid, platform_id=platform.id, status="pending",
        started_at=datetime.now(timezone.utc), triggered_by=triggered_by,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/sync-reviews",
                params={"job_id": job.id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            job.status = "failed"
            job.error_message = f"크롤 워커에 연결할 수 없습니다: {e}"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return job
        if resp.status_code != 200:
            job.status = "failed"
            job.error_message = f"크롤 워커 실행 실패: {resp.text[:500]}"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
        return job

    if background_tasks is not None:
        background_tasks.add_task(run_review_sync_job, job.id)
    return job


@router.post("/store-connections/baemin/sync-reviews", status_code=202)
def start_review_sync(
    background_tasks: BackgroundTasks, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None or conn.credential_ciphertext is None:
        raise HTTPException(400, "먼저 배민 로그인이 필요합니다")

    job = _dispatch_sync_job(sid, platform, conn, db, triggered_by="manual", background_tasks=background_tasks)
    if job is None:
        raise HTTPException(409, "이미 진행 중인 동기화가 있습니다")
    return {"job_id": job.id}


@router.get("/store-connections/baemin/shops")
def list_baemin_shop_brands(
    store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if platform is None:
        return []
    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None:
        return []
    brands = db.scalars(
        select(BaeminShopBrand).where(BaeminShopBrand.connection_id == conn.id).order_by(BaeminShopBrand.shop_no)
    ).all()
    return [{"shop_no": b.shop_no, "shop_name": b.shop_name} for b in brands]


@router.get("/store-connections/baemin/sync-status/{job_id}")
def get_sync_status(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(ReviewSyncJob, job_id, options=[joinedload(ReviewSyncJob.store)])
    if job is None or job.store.user_id != user.id:
        raise HTTPException(404, "작업 없음")
    return {
        "id": job.id, "status": job.status,
        "reviews_fetched": job.reviews_fetched, "reviews_inserted": job.reviews_inserted,
        "error_message": job.error_message,
    }


class InternalBaeminLoginRequest(BaseModel):
    login_id: str
    password: str


@router.post("/internal/baemin-login")
def internal_baemin_login(
    body: InternalBaeminLoginRequest,
    x_worker_secret: str = Header(default=""),
):
    """Railway가 이 컴퓨터(홈 IP)에게 배민 로그인+매장 목록 조회를 위임하는
    서비스 간 엔드포인트(app/routers/ads.py의 /internal/apply-bid와 동일한
    비밀키 계약). 로그인 성공 여부와 매장 목록만 돌려주고, 자격증명 암호화·
    DB 저장은 계속 Railway(baemin_login_endpoint)가 담당한다 — 이 엔드포인트는
    평문 자격증명을 어디에도 저장하지 않는다."""
    _require_worker_secret(x_worker_secret)
    try:
        session = baemin_login(body.login_id, body.password)
    except BaeminLoginError as e:
        raise HTTPException(400, str(e))
    shop_no, shop_name, shops = session.shop_no, session.shop_name, session.shops
    session.close()
    return {
        "shop_no": shop_no,
        "shop_name": shop_name,
        "shops": [{"shop_no": no, "shop_name": name} for no, name in shops],
    }


@router.post("/internal/sync-reviews")
def internal_sync_reviews(
    job_id: int,
    background_tasks: BackgroundTasks,
    x_worker_secret: str = Header(default=""),
):
    """Railway가 이미 만들어둔 ReviewSyncJob(job_id)의 실행만 이 컴퓨터에
    위임하는 서비스 간 엔드포인트. run_review_sync_job은 자체 SessionLocal로
    Railway와 동일한 Postgres에 직접 쓰므로, 진행 상황/결과는 기존
    GET /store-connections/baemin/sync-status/{job_id} 폴링이 그대로 동작한다
    (별도 상태 엔드포인트 불필요)."""
    _require_worker_secret(x_worker_secret)
    background_tasks.add_task(run_review_sync_job, job_id)
    return {"status": "started"}
