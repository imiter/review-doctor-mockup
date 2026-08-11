"""광고 성과(ACoS 실계산) + 광고 순위 모니터링(Mock 스냅샷). CPC 자동입찰 없음.

반경별 순위(distance_km)만 crawler/(Appium 실기기 자동화)로 실측한다.
크롤러는 에뮬레이터가 있는 컴퓨터에서만 돌 수 있다 — 이 프로세스에 로컬
crawler venv가 있으면 직접 실행하고(로컬 개발), 없으면 CRAWL_WORKER_URL로
설정된 다른 컴퓨터(예: 터널로 노출한 개발자 맥북)에 실행을 위임한다(배포
환경, /internal/run-crawl 참고). 두 경우 모두 같은 DB에 결과를 적재하므로
사이트는 어느 쪽이든 결과를 동일하게 조회한다."""

import hmac
import os
import pathlib
import subprocess
import threading
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acos import calculate_performance
from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, BrandAdClickMetric, Order, Platform, Store, User
from scripts.ingest_rank_snapshots import ingest as ingest_csv

router = APIRouter(tags=["ads"])

_CRAWLER_DIR = pathlib.Path(__file__).resolve().parents[3] / "crawler"
_CRAWLER_PYTHON = _CRAWLER_DIR / ".venv" / "bin" / "python"
_CRAWL_TIMEOUT_SEC = 900  # 지점 3개 * 지점당 1분 안팎 + 여유
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
    직접 부르면 배포 환경 프록시 타임아웃에 걸린다 — 아래 _start_crawl_job 참고)."""
    if not _CRAWLER_PYTHON.exists():
        raise HTTPException(500, f"crawler venv를 찾을 수 없습니다: {_CRAWLER_PYTHON}")
    try:
        proc = subprocess.run(
            [str(_CRAWLER_PYTHON), "run_crawl.py"],
            cwd=_CRAWLER_DIR,
            env=_crawler_subprocess_env(),
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


def _campaign_for_user(campaign_id: int, user: User, db: Session) -> AdCampaign:
    campaign = db.get(AdCampaign, campaign_id)
    store = db.get(Store, campaign.store_id) if campaign else None
    if campaign is None or store is None or store.user_id != user.id:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return campaign


@router.post("/ads/rank-by-distance/run")
def ads_rank_by_distance_run(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()

    result = []
    for c in campaigns:
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
