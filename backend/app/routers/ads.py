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
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acos import calculate_performance
from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, Order, Store, User
from scripts.ingest_rank_snapshots import ingest as ingest_csv

router = APIRouter(tags=["ads"])

_CRAWLER_DIR = pathlib.Path(__file__).resolve().parents[3] / "crawler"
_CRAWLER_PYTHON = _CRAWLER_DIR / ".venv" / "bin" / "python"
_CRAWL_TIMEOUT_SEC = 900  # 지점 3개 * 지점당 1분 안팎 + 여유
_crawl_lock = threading.Lock()  # 에뮬레이터는 한 번에 하나만 조작 가능 — 동시 실행 방지

_CRAWL_WORKER_URL = os.getenv("CRAWL_WORKER_URL")  # 배포 환경에서만 설정 (예: 터널 URL)
_CRAWL_WORKER_SECRET = os.getenv("CRAWL_WORKER_SECRET", "")  # 워커 호출 시/검증 시 공용


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
    결과를 DB에 적재한다. (inserted, skipped) 개수를 반환한다.

    호출부가 이미 _crawl_lock을 잡고 있다고 가정한다 — 이 함수 자체는 락을
    걸지 않는다(로컬 직접 실행 경로와 /internal/run-crawl 양쪽에서 재사용하기
    위해 락 범위를 호출부 책임으로 뺐다)."""
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


@router.post("/internal/run-crawl")
def internal_run_crawl(
    campaign_id: int,
    x_worker_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """다른 배포 인스턴스(예: Railway)가 이 컴퓨터에게 "실제로 크롤링해줘"라고
    요청하는 서비스 간(service-to-service) 엔드포인트. 로그인한 사용자의 JWT가
    아니라 CRAWL_WORKER_SECRET 공유 비밀로만 인증한다 — 이 컴퓨터가 곧 크롤
    실행 주체이므로 "누가 로그인했는지"는 이 단계에서 의미가 없다(사용자
    권한 검사는 이미 호출부인 /ads/rank-by-distance/run에서 끝났다).

    이 엔드포인트는 CRAWL_WORKER_SECRET이 설정된 컴퓨터(=워커로 쓰려는
    컴퓨터)에서만 의미가 있다 — 비어 있으면 그 자체로 거절한다(빈 문자열끼리
    비교해서 통과하는 사고를 막는다)."""
    if not _CRAWL_WORKER_SECRET or not hmac.compare_digest(x_worker_secret, _CRAWL_WORKER_SECRET):
        raise HTTPException(403, "워커 비밀키가 일치하지 않습니다")

    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")

    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    try:
        inserted, skipped = _run_local_crawl(campaign.id)
    finally:
        _crawl_lock.release()

    return {"inserted": inserted, "skipped": skipped, "points": _latest_distance_points(db, campaign)}


@router.post("/ads/rank-by-distance/run")
def ads_rank_by_distance_run(
    campaign_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """반경별 순위를 실제로 실측하고 ad_rank_snapshots에 적재한 뒤 최신 값을
    반환한다. 사용자 로그인(JWT)로 인증하고, 실행 주체는 두 갈래로 나뉜다:

    1. 이 프로세스와 같은 컴퓨터에 crawler venv가 있으면(로컬 개발) 직접 실행.
    2. 없고 CRAWL_WORKER_URL이 설정돼 있으면(배포 환경) 그 URL의
       /internal/run-crawl로 실행을 위임하고 응답을 그대로 전달한다. 워커가
       이 백엔드와 같은 DB(Postgres)에 결과를 적재하므로, 위임이 끝난 뒤 이
       프로세스의 DB 세션으로 다시 조회하면 최신 값이 바로 보인다.

    지점 3개 * 지점당 1분 안팎으로 완료까지 수 분 걸리는 동기 호출이다 —
    응답이 오기 전까지 호출부(프론트엔드)가 버튼을 비활성화해야 한다."""
    campaign = db.get(AdCampaign, campaign_id)
    store = db.get(Store, campaign.store_id) if campaign else None
    if campaign is None or store is None or store.user_id != user.id:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")

    if _CRAWLER_PYTHON.exists():
        if not _crawl_lock.acquire(blocking=False):
            raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
        try:
            inserted, skipped = _run_local_crawl(campaign.id)
        finally:
            _crawl_lock.release()
        return {"inserted": inserted, "skipped": skipped, "points": _latest_distance_points(db, campaign)}

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/run-crawl",
                params={"campaign_id": campaign.id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=_CRAWL_TIMEOUT_SEC + 30,
            )
        except httpx.RequestError as e:
            raise HTTPException(502, f"크롤 워커에 연결할 수 없습니다: {e}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"크롤 워커 실행 실패: {resp.text[:500]}")
        return {**resp.json(), "points": _latest_distance_points(db, campaign)}

    raise HTTPException(500, "이 환경에서는 실측 크롤링을 실행할 수 없습니다 (로컬 crawler venv도 CRAWL_WORKER_URL도 없음)")


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
