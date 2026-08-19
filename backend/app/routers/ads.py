"""광고 성과(ACoS 실계산) + 광고 순위 모니터링(Mock 스냅샷). CPC 자동입찰 없음.

반경별 순위(distance_km)만 crawler/(Appium 실기기 자동화)로 실측한다.
크롤러는 에뮬레이터가 있는 컴퓨터에서만 돌 수 있다 — 이 프로세스에 로컬
crawler venv가 있으면 직접 실행하고(로컬 개발), 없으면 CRAWL_WORKER_URL로
설정된 다른 컴퓨터(예: 터널로 노출한 개발자 맥북)에 실행을 위임한다(배포
환경, /internal/run-crawl 참고). 두 경우 모두 같은 DB에 결과를 적재하므로
사이트는 어느 쪽이든 결과를 동일하게 조회한다."""

import hmac
import logging
import os
import pathlib
import subprocess
import threading
import time
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acos import calculate_performance
from app.auth import get_user_default_store_id
from app.credential_crypto import decrypt_credential
from app.db import SessionLocal, get_db
from app.models import (
    AdCampaign,
    AdPerformanceMetric,
    AdRankSnapshot,
    BrandAdClickMetric,
    Order,
    Platform,
    Store,
    StorePlatformConnection,
    User,
)
from app.plan import require_pro_plan
from scrapers.baemin_ads import submit_cpc_bid
from scrapers.baemin_auth import login as baemin_login
from scrapers.baemin_stats import fetch_shop_info
from scripts.ingest_rank_snapshots import ingest as ingest_csv

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ads"])

_CRAWLER_DIR = pathlib.Path(__file__).resolve().parents[3] / "crawler"
_CRAWLER_PYTHON = _CRAWLER_DIR / ".venv" / "bin" / "python"
_CRAWL_TIMEOUT_SEC = 900  # 지점 3개 * 지점당 1분 안팎 + 여유
_BID_STEP_WON = 30  # 순위 미달 시 다음 시도 추천 증액폭(설계 문서 — 사용자 제안 10~50원 중 기본값)
_BID_APPLY_WAIT_SEC = 30  # 배민 쪽 반영 시차 대기(설계 문서 — 화면 안내문구 확인, 구현 시 조정 가능)
_crawl_lock = threading.Lock()  # 에뮬레이터는 한 번에 하나만 조작 가능 — 동시 실행 방지

_CRAWL_WORKER_URL = os.getenv("CRAWL_WORKER_URL")  # 배포 환경에서만 설정 (예: 터널 URL)
_CRAWL_WORKER_SECRET = os.getenv("CRAWL_WORKER_SECRET", "")  # 워커 호출 시/검증 시 공용

# 크롤은 3~5분 걸리는데, Railway 등 배포 환경 앞단의 프록시(엣지)는 보통
# 100초 안팎에서 오래 걸리는 요청을 강제로 끊는다(실측: 524 Timeout 확인).
# 그래서 "요청 하나로 끝까지 기다리는" 구조 대신 "시작만 시키고(POST) 상태를
# 짧은 간격으로 조회(GET)하는" 구조로 만든다 — 모든 개별 요청은 즉시 끝난다.
_job_state_lock = threading.Lock()
_job_state: dict = {"campaign_id": None, "status": None, "inserted": None, "skipped": None, "error": None}


def _crawler_subprocess_env() -> dict:
    """adb가 PATH에 없는 환경(예: 셸 프로파일을 안 읽는 프로세스)에서도 실행되도록
    Android platform-tools 경로를 PATH에 보강한다 — 실측으로 이 문제가 확인됐다."""
    env = os.environ.copy()
    android_home = env.get("ANDROID_HOME") or str(pathlib.Path.home() / "Library" / "Android" / "sdk")
    candidates = [android_home, "/opt/homebrew/share/android-commandlinetools"]
    extra = os.pathsep.join(str(pathlib.Path(c) / "platform-tools") for c in candidates)
    env["PATH"] = f"{extra}{os.pathsep}{env.get('PATH', '')}"
    return env


def _latest_distance_points(db: Session, campaign: AdCampaign) -> list[dict]:
    snapshots = db.scalars(
        select(AdRankSnapshot)
        .where(AdRankSnapshot.campaign_id == campaign.id, AdRankSnapshot.distance_km.is_not(None))
        .order_by(AdRankSnapshot.snapshot_at.desc())
    ).all()
    latest_by_point: dict[str, AdRankSnapshot] = {}
    for s in snapshots:
        latest_by_point.setdefault(s.point_label, s)
    points = sorted(latest_by_point.values(), key=lambda s: s.distance_km)
    return [
        {
            "point_label": p.point_label,
            "distance_km": float(p.distance_km),
            "current_rank": p.current_rank,
            "total_scanned": p.total_scanned,
            "ads_above": p.ads_above,
            "snapshot_at": p.snapshot_at.isoformat(),
        }
        for p in points
    ]


@router.get("/ads/performance")
def ads_performance(
    store_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()
    since = date.today() - timedelta(days=days)

    total_orders = db.scalar(
        select(func.count(Order.id)).where(Order.store_id == sid, Order.ordered_at >= since)
    )

    result = []
    for c in campaigns:
        if c.shop_no:
            # shop_no가 있으면(실데이터 캠페인) 이미 실측인 BrandAdClickMetric을
            # 쓴다 — /ads/click-performance가 하는 것과 동일한 조회, Mock인
            # ad_performance_metrics는 아예 조회하지 않는다.
            baemin_platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
            agg = db.execute(
                select(
                    func.coalesce(func.sum(BrandAdClickMetric.ad_spend), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.clicks), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.ad_orders), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.ad_revenue), 0),
                ).where(
                    BrandAdClickMetric.store_id == sid,
                    BrandAdClickMetric.platform_id == baemin_platform.id,
                    BrandAdClickMetric.shop_no == c.shop_no,
                    BrandAdClickMetric.metric_date >= since,
                )
            ).one() if baemin_platform else (0, 0, 0, 0)
        else:
            agg = db.execute(
                select(
                    func.coalesce(func.sum(AdPerformanceMetric.ad_spend), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.clicks), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.ad_orders), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.ad_revenue), 0),
                ).where(AdPerformanceMetric.campaign_id == c.id, AdPerformanceMetric.metric_date >= since)
            ).one()
        perf = calculate_performance(*agg)
        order_share = round(perf.ad_orders / total_orders, 4) if total_orders else None
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "status": c.status,
            "period_days": days,
            "ad_spend": perf.ad_spend,
            "clicks": perf.clicks,
            "ad_orders": perf.ad_orders,
            "ad_revenue": perf.ad_revenue,
            "cpc": perf.cpc,
            "cvr": perf.cvr,
            "aov": perf.aov,
            "acos": perf.acos,
            "score": perf.score,
            "order_share": order_share,
        })
    return result


@router.get("/ads/click-performance")
def ads_click_performance(
    shop_no: str,
    store_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """브랜드(shop_no) 단위 우리가게클릭 실데이터 성과. 기존 `/ads/performance`
    (카테고리 기반 `ad_campaigns`, Mock)와 완전히 별개 — `ad_campaigns`를
    전혀 참조하지 않는다."""
    sid = store_id or get_user_default_store_id(user, db)
    since = date.today() - timedelta(days=days)

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    if platform is None:
        ad_spend, impressions, clicks, ad_orders, ad_revenue = 0, 0, 0, 0, 0
    else:
        agg = db.execute(
            select(
                func.coalesce(func.sum(BrandAdClickMetric.ad_spend), 0),
                func.coalesce(func.sum(BrandAdClickMetric.impressions), 0),
                func.coalesce(func.sum(BrandAdClickMetric.clicks), 0),
                func.coalesce(func.sum(BrandAdClickMetric.ad_orders), 0),
                func.coalesce(func.sum(BrandAdClickMetric.ad_revenue), 0),
            ).where(
                BrandAdClickMetric.store_id == sid,
                BrandAdClickMetric.platform_id == platform.id,
                BrandAdClickMetric.shop_no == shop_no,
                BrandAdClickMetric.metric_date >= since,
            )
        ).one()
        ad_spend, impressions, clicks, ad_orders, ad_revenue = agg
    perf = calculate_performance(ad_spend, clicks, ad_orders, ad_revenue)

    return {
        "shop_no": shop_no,
        "period_days": days,
        "ad_spend": perf.ad_spend,
        "impressions": impressions,
        "clicks": perf.clicks,
        "ad_orders": perf.ad_orders,
        "ad_revenue": perf.ad_revenue,
        "cpc": perf.cpc,
        "cvr": perf.cvr,
        "aov": perf.aov,
        "acos": perf.acos,
        "score": perf.score,
    }


@router.get("/ads/rank-by-distance")
def ads_rank_by_distance(
    store_id: int | None = None,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """가게 기준 반경별(0km/1.5~2.5km/2.5~3.5km) 카테고리 순위.

    crawler/(Appium 실기기 자동화)로 실측 수집해 ingest_rank_snapshots.py가
    적재한 distance_km IS NOT NULL 행만 대상으로 한다 — 요청 시점에 실시간으로
    크롤링하지 않고 DB에 이미 적재된 값을 조회만 한다."""
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()

    return [
        {
            "campaign_id": c.id,
            "category": c.category,
            "target_rank": c.target_rank,
            "points": _latest_distance_points(db, c),
        }
        for c in campaigns
    ]


def _run_local_crawl(campaign_id: int) -> tuple[int, int]:
    """이 프로세스와 같은 컴퓨터의 crawler venv/에뮬레이터로 실제 크롤링을 실행하고
    결과를 DB에 적재한다. (inserted, skipped) 개수를 반환한다. 3~5분 걸리는
    블로킹 호출이므로 반드시 백그라운드 스레드에서만 부른다(요청 핸들러에서
    직접 부르면 배포 환경 프록시 타임아웃에 걸린다 — 아래 _start_crawl_job 참고).

    캠페인에 shop_no가 있으면(실데이터 캠페인, 지금은 치밥대장뿐) 크롤러
    실행 전에 이 프로세스가 이미 갖고 있는 배민 인증 흐름으로 로그인해
    fetch_shop_info로 실제 상호명/카테고리/주소/좌표를 가져와 크롤러
    서브프로세스의 환경변수로 넘긴다 — crawler/.env 파일은 건드리지 않는다.
    이 단계가 실패하면 크롤 자체를 하드 에러로 중단한다(.env로 조용히
    폴백하면 엉뚱한 가게를 실측한 결과가 이 캠페인 결과로 저장될 위험이
    있다). shop_no가 없는 캠페인(예: 닭갈비연구소)은 이 단계를 완전히
    건너뛰고 기존처럼 crawler/.env 값을 그대로 쓴다."""
    if not _CRAWLER_PYTHON.exists():
        raise HTTPException(500, f"crawler venv를 찾을 수 없습니다: {_CRAWLER_PYTHON}")

    env = _crawler_subprocess_env()
    db = SessionLocal()
    try:
        campaign = db.get(AdCampaign, campaign_id)
        if campaign is not None and campaign.shop_no:
            baemin_platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
            conn = db.scalar(
                select(StorePlatformConnection).where(
                    StorePlatformConnection.store_id == campaign.store_id,
                    StorePlatformConnection.platform_id == baemin_platform.id,
                )
            ) if baemin_platform else None
            if conn is None:
                raise HTTPException(500, f"캠페인 {campaign_id}의 배민 연결을 찾을 수 없습니다")
            try:
                credential = decrypt_credential(conn.credential_ciphertext)
                session = baemin_login(credential["login_id"], credential["password"])
                try:
                    info = fetch_shop_info(session.page, campaign.shop_no)
                finally:
                    session.close()
            except Exception as e:
                raise HTTPException(502, f"가게 정보 조회에 실패해 크롤을 시작하지 않았습니다: {e}") from e
            env["STORE_DISPLAY_NAME"] = info["name"]
            env["CATEGORY_LABEL"] = info["category"]
            env["STORE_ADDRESS"] = info["road_address"]
            env["STORE_LAT"] = str(info["latitude"])
            env["STORE_LNG"] = str(info["longitude"])
    finally:
        db.close()

    try:
        proc = subprocess.run(
            [str(_CRAWLER_PYTHON), "run_crawl.py"],
            cwd=_CRAWLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=_CRAWL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "순위 확인이 제한 시간(15분) 안에 끝나지 않았습니다 — 에뮬레이터/Appium 서버 상태를 확인하세요.")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-2000:]
        raise HTTPException(502, f"크롤러 실행 실패:\n{detail}")

    csv_path = _CRAWLER_DIR / "output" / "results.csv"
    return ingest_csv(csv_path, campaign_id)


def _apply_bid_then_crawl(campaign_id: int, amount: int) -> tuple[int, int]:
    """입찰가를 배민에 실제로 반영한 뒤(쓰기, submit_cpc_bid), 배민 쪽 반영
    시차를 기다렸다가(_BID_APPLY_WAIT_SEC) 기존 _run_local_crawl로 순위를
    재측정한다. 반드시 백그라운드 스레드에서만 호출한다(로그인+제출+대기+
    크롤이 합쳐 수십 초~수 분 걸리는 블로킹 호출)."""
    db = SessionLocal()
    try:
        campaign = db.get(AdCampaign, campaign_id)
        if campaign is None or not campaign.shop_no:
            raise HTTPException(500, f"캠페인 {campaign_id}은 실데이터 캠페인이 아니라 입찰가를 반영할 수 없습니다")
        baemin_platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
        conn = db.scalar(
            select(StorePlatformConnection).where(
                StorePlatformConnection.store_id == campaign.store_id,
                StorePlatformConnection.platform_id == baemin_platform.id,
            )
        ) if baemin_platform else None
        if conn is None:
            raise HTTPException(500, f"캠페인 {campaign_id}의 배민 연결을 찾을 수 없습니다")
        try:
            credential = decrypt_credential(conn.credential_ciphertext)
            session = baemin_login(credential["login_id"], credential["password"])
            try:
                submit_cpc_bid(session.page, campaign.shop_no, amount)
            finally:
                session.close()
        except Exception as e:
            logger.exception("submit_cpc_bid 실패 campaign_id=%s amount=%s", campaign_id, amount)
            raise HTTPException(502, f"입찰가 반영에 실패했습니다: {e}") from e

        try:
            campaign.current_cpc = amount
            db.commit()
        except Exception as e:
            raise HTTPException(
                500,
                f"입찰가는 배민에 실제로 반영됐지만({amount}원), 내부 기록 갱신에 실패했습니다. "
                f"화면에 표시되는 현재 CPC가 실제 값과 다를 수 있습니다: {e}",
            ) from e
    finally:
        db.close()

    time.sleep(_BID_APPLY_WAIT_SEC)
    if _CRAWLER_PYTHON.exists():
        try:
            return _run_local_crawl(campaign_id)
        except HTTPException as e:
            raise HTTPException(
                e.status_code,
                f"입찰가({amount}원)는 이미 배민에 정상 반영됐습니다. 다만 순위 재측정에는 실패했습니다: {e.detail}",
            ) from e

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/run-crawl",
                params={"campaign_id": campaign_id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            raise HTTPException(
                502,
                f"입찰가({amount}원)는 이미 배민에 정상 반영됐습니다. 다만 크롤 워커에 연결할 수 없어 순위 재측정을 시작하지 못했습니다: {e}",
            ) from e
        if resp.status_code != 200:
            raise HTTPException(
                resp.status_code,
                f"입찰가({amount}원)는 이미 배민에 정상 반영됐습니다. 다만 크롤 워커 실행에 실패해 순위 재측정을 시작하지 못했습니다: {resp.text[:500]}",
            )
        # 워커에 위임한 뒤에는 이 백그라운드 스레드가 결과를 기다리지 않는다 —
        # 진행상황/결과는 GET /ads/rank-by-distance/run/status가 이미 _CRAWL_WORKER_URL이
        # 설정된 경우 이 프로세스의 _job_state를 보지 않고 워커에 직접 물어보도록 돼
        # 있으므로(ads_rank_by_distance_run_status 참고), 여기서는 위임 성공만 확인하면
        # 충분하다. 반환값은 이 프로세스 기준으로는 의미가 없다(워커가 실제 값을 안다).
        return (0, 0)

    raise HTTPException(
        500,
        f"입찰가({amount}원)는 이미 배민에 정상 반영됐습니다. 다만 이 환경에서는 순위 재측정을 실행할 수 없습니다 "
        f"(로컬 crawler venv도 CRAWL_WORKER_URL도 없음).",
    )


def _execute_bid_apply_job(campaign_id: int, amount: int) -> None:
    """백그라운드 스레드에서 실행된다. _crawl_lock은 호출부(apply-bid 엔드포인트)가
    이미 잡아뒀고, 끝나면 이 함수가 반드시 놓아준다(_execute_crawl_job과 동일 계약)."""
    try:
        inserted, skipped = _apply_bid_then_crawl(campaign_id, amount)
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="done", inserted=inserted, skipped=skipped, error=None)
    except HTTPException as e:
        logger.warning("apply-bid 실패 campaign_id=%s: %s", campaign_id, e.detail)
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=e.detail)
    except Exception as e:  # noqa: BLE001
        logger.exception("apply-bid 처리 중 예상치 못한 예외 campaign_id=%s", campaign_id)
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=str(e))
    finally:
        _crawl_lock.release()


def _execute_crawl_job(campaign_id: int) -> None:
    """백그라운드 스레드에서 실행된다. _crawl_lock은 호출부(_start_crawl_job)가
    이미 잡아뒀고, 끝나면 이 함수가 반드시 놓아준다."""
    try:
        inserted, skipped = _run_local_crawl(campaign_id)
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="done", inserted=inserted, skipped=skipped, error=None)
    except HTTPException as e:
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=e.detail)
    except Exception as e:  # noqa: BLE001 — 백그라운드 스레드라 여기서 못 잡으면 조용히 사라진다
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=str(e))
    finally:
        _crawl_lock.release()


def _start_crawl_job(campaign_id: int, background_tasks: BackgroundTasks) -> None:
    """크롤을 백그라운드로 시작만 하고 즉시 반환한다(응답 자체는 몇 ms 안에 끝남).
    진행 상황은 _job_state를 폴링(/internal/run-crawl/status,
    /ads/rank-by-distance/run/status)해서 확인한다."""
    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    with _job_state_lock:
        _job_state.update(campaign_id=campaign_id, status="running", inserted=None, skipped=None, error=None)
    background_tasks.add_task(_execute_crawl_job, campaign_id)


def _job_status_response(campaign_id: int, db: Session, campaign: AdCampaign) -> dict:
    with _job_state_lock:
        state = dict(_job_state)
    if state["campaign_id"] != campaign_id:
        return {"status": "idle"}
    if state["status"] == "done":
        return {"status": "done", "inserted": state["inserted"], "skipped": state["skipped"], "points": _latest_distance_points(db, campaign)}
    if state["status"] == "error":
        return {"status": "error", "error": state["error"]}
    return {"status": "running"}


def _require_worker_secret(x_worker_secret: str) -> None:
    if not _CRAWL_WORKER_SECRET or not hmac.compare_digest(x_worker_secret, _CRAWL_WORKER_SECRET):
        raise HTTPException(403, "워커 비밀키가 일치하지 않습니다")


@router.post("/internal/run-crawl")
def internal_run_crawl(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    x_worker_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """다른 배포 인스턴스(예: Railway)가 이 컴퓨터에게 "실제로 크롤링해줘"라고
    요청하는 서비스 간(service-to-service) 엔드포인트. 로그인한 사용자의 JWT가
    아니라 CRAWL_WORKER_SECRET 공유 비밀로만 인증한다 — 이 컴퓨터가 곧 크롤
    실행 주체이므로 "누가 로그인했는지"는 이 단계에서 의미가 없다(사용자
    권한 검사는 이미 호출부인 /ads/rank-by-distance/run에서 끝났다).

    시작만 시키고 바로 반환한다 — 실제 진행 상황은 /internal/run-crawl/status로
    확인한다(배포 환경 프록시가 오래 걸리는 요청을 끊어버리는 문제 때문에
    "요청 하나로 끝까지 기다리는" 구조를 쓸 수 없다)."""
    _require_worker_secret(x_worker_secret)
    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    _start_crawl_job(campaign.id, background_tasks)
    return {"status": "started"}


@router.get("/internal/run-crawl/status")
def internal_run_crawl_status(
    campaign_id: int,
    x_worker_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    _require_worker_secret(x_worker_secret)
    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return _job_status_response(campaign_id, db, campaign)


@router.post("/internal/apply-bid")
def internal_apply_bid(
    campaign_id: int,
    amount: int,
    background_tasks: BackgroundTasks,
    x_worker_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """internal_run_crawl과 동일한 서비스 간 엔드포인트 계약이지만, 크롤
    재측정뿐 아니라 배민 로그인 + 입찰가 제출(submit_cpc_bid) 전체를 이
    컴퓨터에서 실행한다. Railway(클라우드 IP)에서 직접 배민 로그인을
    시도하면 로그인 폼 자체가 렌더링되지 않고 fill()이 30초 타임아웃으로
    죽는 현상이 실측 확인됐다(2026-08-18, 봇 탐지로 추정) — 리뷰/매출
    스크래핑과 동일하게 이 컴퓨터(홈 IP)에서 로그인부터 실행해야 한다.
    진행 상황/결과는 기존 GET /internal/run-crawl/status와 같은 _job_state를
    공유하므로 그대로 폴링된다."""
    _require_worker_secret(x_worker_secret)
    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    with _job_state_lock:
        _job_state.update(campaign_id=campaign_id, status="running", inserted=None, skipped=None, error=None)
    background_tasks.add_task(_execute_bid_apply_job, campaign_id, amount)
    return {"status": "started"}


def _campaign_for_user(campaign_id: int, user: User, db: Session) -> AdCampaign:
    campaign = db.get(AdCampaign, campaign_id)
    store = db.get(Store, campaign.store_id) if campaign else None
    if campaign is None or store is None or store.user_id != user.id:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return campaign


class UpdateCampaignRequest(BaseModel):
    target_rank: int = Field(ge=1)


@router.patch("/ads/campaigns/{campaign_id}")
def ads_update_campaign(
    campaign_id: int,
    body: UpdateCampaignRequest,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    campaign = _campaign_for_user(campaign_id, user, db)
    campaign.target_rank = body.target_rank
    db.commit()
    return {"campaign_id": campaign.id, "target_rank": campaign.target_rank}


@router.post("/ads/rank-by-distance/apply-bid")
def ads_apply_bid(
    campaign_id: int,
    amount: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """사용자가 목표 금액을 직접 확인·클릭했을 때만 호출된다(프론트엔드가
    "OO원으로 배민에 실제 반영됩니다" 확인 다이얼로그를 거친다). 성공하면
    기존 POST /ads/rank-by-distance/run과 동일한 {"status": "started"} 계약으로
    응답하고, 진행 상황/결과는 기존 GET /ads/rank-by-distance/run/status를
    그대로 폴링해서 확인한다(새 상태 엔드포인트를 만들지 않는다).

    실행 주체는 ads_rank_by_distance_run과 동일한 두 갈래다 — 다만 이 경우는
    배민 로그인 자체(submit_cpc_bid 이전 단계)가 Railway 같은 클라우드 IP에서는
    봇 탐지로 실패하는 게 실측 확인됐으므로(2026-08-18), 로컬 crawler venv가
    없으면 크롤만이 아니라 로그인+입찰 제출 전체를 워커에 위임한다(로컬
    _apply_bid_then_crawl을 이 프로세스에서 절대 실행하지 않는다)."""
    campaign = _campaign_for_user(campaign_id, user, db)
    if not campaign.shop_no:
        raise HTTPException(400, "실데이터 캠페인만 입찰가를 반영할 수 있습니다")
    if amount < 1:
        raise HTTPException(400, "입찰 금액은 1원 이상이어야 합니다")

    if _CRAWLER_PYTHON.exists():
        if not _crawl_lock.acquire(blocking=False):
            raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="running", inserted=None, skipped=None, error=None)
        background_tasks.add_task(_execute_bid_apply_job, campaign_id, amount)
        return {"status": "started"}

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/apply-bid",
                params={"campaign_id": campaign_id, "amount": amount},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            raise HTTPException(502, f"크롤 워커에 연결할 수 없습니다: {e}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"크롤 워커 실행 실패: {resp.text[:500]}")
        return resp.json()

    raise HTTPException(500, "이 환경에서는 입찰가 반영을 실행할 수 없습니다 (로컬 crawler venv도 CRAWL_WORKER_URL도 없음)")


@router.post("/ads/rank-by-distance/run")
def ads_rank_by_distance_run(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """반경별 순위 실측을 시작만 시키고 즉시 반환한다({"status": "started"}).
    실제 진행 상황·완료 결과는 GET /ads/rank-by-distance/run/status를
    몇 초 간격으로 폴링해서 확인한다(프론트엔드가 이미 그렇게 구현돼 있다).

    실행 주체는 두 갈래:
    1. 이 프로세스와 같은 컴퓨터에 crawler venv가 있으면(로컬 개발) 직접 시작.
    2. 없고 CRAWL_WORKER_URL이 설정돼 있으면(배포 환경) 그 URL의
       /internal/run-crawl을 호출해 실행을 위임한다. 이 호출도 "시작만
       시키는" 즉시 반환 요청이라 배포 환경 프록시 타임아웃과 무관하다."""
    campaign = _campaign_for_user(campaign_id, user, db)

    if _CRAWLER_PYTHON.exists():
        _start_crawl_job(campaign.id, background_tasks)
        return {"status": "started"}

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/run-crawl",
                params={"campaign_id": campaign.id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            raise HTTPException(502, f"크롤 워커에 연결할 수 없습니다: {e}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"크롤 워커 실행 실패: {resp.text[:500]}")
        return resp.json()

    raise HTTPException(500, "이 환경에서는 실측 크롤링을 실행할 수 없습니다 (로컬 crawler venv도 CRAWL_WORKER_URL도 없음)")


@router.get("/ads/rank-by-distance/run/status")
def ads_rank_by_distance_run_status(
    campaign_id: int,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """/ads/rank-by-distance/run이 시작시킨 크롤의 진행 상황을 조회한다.
    {"status": "idle" | "running" | "done" | "error", ...}"""
    campaign = _campaign_for_user(campaign_id, user, db)

    if _CRAWLER_PYTHON.exists():
        return _job_status_response(campaign_id, db, campaign)

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.get(
                f"{_CRAWL_WORKER_URL}/internal/run-crawl/status",
                params={"campaign_id": campaign.id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            raise HTTPException(502, f"크롤 워커에 연결할 수 없습니다: {e}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"크롤 워커 상태 확인 실패: {resp.text[:500]}")
        data = resp.json()
        if data.get("status") == "done":
            # 워커가 이미 같은 DB(Postgres)에 커밋했으므로 이 프로세스에서
            # 다시 조회해도 최신 값이 바로 보인다.
            return {**data, "points": _latest_distance_points(db, campaign)}
        return data

    return {"status": "idle"}


@router.get("/ads/rank-monitoring")
def ads_rank_monitoring(
    store_id: int | None = None,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()

    result = []
    for c in campaigns:
        if c.shop_no:
            # shop_no가 있으면(실데이터 캠페인) 시간별 Mock 스냅샷이 아니라
            # 반경별 실측(distance_km=0, 가게 주소 지점)의 가장 최근 값을
            # "현재 순위"로 쓴다 — competitor_est_cpc는 실측이 구조적으로
            # 불가능해 계속 추정치로 남긴다(아래).
            real_latest = db.scalar(
                select(AdRankSnapshot)
                .where(AdRankSnapshot.campaign_id == c.id, AdRankSnapshot.distance_km == 0)
                .order_by(AdRankSnapshot.snapshot_at.desc())
                .limit(1)
            )
            current_rank = real_latest.current_rank if real_latest else None
            rank_status = ("rank_dropped" if current_rank > c.target_rank else "normal") if current_rank is not None else None
            recommended_action = "raise_cpc" if rank_status == "rank_dropped" else "keep"
            suggested_cpc = c.current_cpc + _BID_STEP_WON if rank_status == "rank_dropped" else None
            result.append({
                "campaign_id": c.id,
                "category": c.category,
                "current_cpc": c.current_cpc,
                "target_rank": c.target_rank,
                "status": c.status,
                "current_rank": current_rank,
                "competitor_est_cpc": None,  # 배민이 노출하지 않아 실측 불가 — 항상 None(프론트가 컬럼 자체를 제거)
                "rank_status": rank_status,
                "recommended_action": recommended_action,
                "suggested_cpc": suggested_cpc,
                "snapshot_at": real_latest.snapshot_at.isoformat() if real_latest else None,
            })
            continue

        latest = db.scalar(
            select(AdRankSnapshot)
            .where(AdRankSnapshot.campaign_id == c.id)
            .order_by(AdRankSnapshot.snapshot_at.desc())
            .limit(1)
        )
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "target_rank": c.target_rank,
            "status": c.status,
            "current_rank": latest.current_rank if latest else None,
            "competitor_est_cpc": latest.competitor_est_cpc if latest else None,
            "rank_status": latest.status if latest else None,
            "recommended_action": latest.recommended_action if latest else "keep",
            "suggested_cpc": latest.suggested_cpc if latest else None,
            "snapshot_at": latest.snapshot_at.isoformat() if latest else None,
        })
    return result
