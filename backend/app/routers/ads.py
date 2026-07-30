"""광고 성과(ACoS 실계산) + 광고 순위 모니터링(Mock 스냅샷). CPC 자동입찰 없음.

반경별 순위(distance_km)만 crawler/(Appium 실기기 자동화)로 실측한다 —
/ads/rank-by-distance/run이 crawler를 실제로 실행시키는 유일한 경로다."""

import os
import pathlib
import subprocess
import threading
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.post("/ads/rank-by-distance/run")
def ads_rank_by_distance_run(
    campaign_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """crawler/를 실제로 실행해(Appium 실기기 자동화) 반경별 순위를 실측하고
    ad_rank_snapshots에 적재한 뒤 최신 값을 반환한다.

    지점 3개 * 지점당 1분 안팎으로 완료까지 수 분 걸리는 동기 호출이다 —
    응답이 오기 전까지 호출부(프론트엔드)가 버튼을 비활성화해야 한다.
    에뮬레이터는 한 번에 하나만 조작 가능해 동시 요청은 409로 거절한다."""
    campaign = db.get(AdCampaign, campaign_id)
    store = db.get(Store, campaign.store_id) if campaign else None
    if campaign is None or store is None or store.user_id != user.id:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")

    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    try:
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
        inserted, skipped = ingest_csv(csv_path, campaign.id)
    finally:
        _crawl_lock.release()

    return {"inserted": inserted, "skipped": skipped, "points": _latest_distance_points(db, campaign)}


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
