from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdBidHistory, AdCampaign, AdRankSnapshot, AdRecommendation, MockClock

router = APIRouter(prefix="/api", tags=["ads"])


def _clock(db: Session) -> MockClock:
    clock = db.get(MockClock, 1)
    if clock is None:
        raise HTTPException(500, "mock_clock 미초기화 — seed를 먼저 실행하세요")
    return clock


def _latest_snapshot(db: Session, campaign_id: int, mock_now) -> AdRankSnapshot | None:
    return db.scalar(
        select(AdRankSnapshot)
        .where(AdRankSnapshot.campaign_id == campaign_id, AdRankSnapshot.snapshot_at <= mock_now)
        .order_by(AdRankSnapshot.snapshot_at.desc())
        .limit(1)
    )


def _pending_rec(db: Session, campaign_id: int) -> AdRecommendation | None:
    return db.scalar(
        select(AdRecommendation).where(
            AdRecommendation.campaign_id == campaign_id, AdRecommendation.status == "pending"
        )
    )


@router.get("/ad-campaigns")
def dashboard(db: Session = Depends(get_db)):
    clock = _clock(db)
    campaigns = db.scalars(select(AdCampaign).order_by(AdCampaign.id)).all()
    rows = []
    for c in campaigns:
        snap = _latest_snapshot(db, c.id, clock.mock_now)
        rec = _pending_rec(db, c.id)
        rows.append({
            "id": c.id,
            "store_name": c.store_platform.store.name,
            "platform_name": c.store_platform.platform.name,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "target_rank": c.target_rank,
            "my_rank": snap.my_rank if snap else None,
            "competitor_est_cpc": snap.competitor_est_cpc if snap else None,
            "status": c.status,
            "recommendation": (
                {"id": rec.id, "action_type": rec.action_type, "suggested_cpc": rec.suggested_cpc}
                if rec else None
            ),
        })
    return {"mock_now": clock.mock_now.isoformat(), "campaigns": rows}


@router.post("/ads/refresh")
def refresh(db: Session = Depends(get_db)):
    clock = _clock(db)
    clock.mock_now = clock.mock_now + timedelta(minutes=10)
    for c in db.scalars(select(AdCampaign).where(AdCampaign.status == "active")).all():
        snap = _latest_snapshot(db, c.id, clock.mock_now)
        if snap and snap.my_rank > c.target_rank and _pending_rec(db, c.id) is None:
            db.add(AdRecommendation(
                campaign_id=c.id, snapshot_id=snap.id,
                action_type="raise_cpc", suggested_cpc=snap.competitor_est_cpc + 50,
                status="pending", created_at=clock.mock_now,
            ))
    db.commit()
    return {"mock_now": clock.mock_now.isoformat()}


@router.post("/ad-recommendations/{rec_id}/apply")
def apply_recommendation(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(AdRecommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "추천 없음")
    if rec.status != "pending":
        raise HTTPException(409, "대기 상태 추천만 적용 가능")
    campaign = db.get(AdCampaign, rec.campaign_id)
    clock = _clock(db)
    db.add(AdBidHistory(
        campaign_id=campaign.id, recommendation_id=rec.id,
        old_cpc=campaign.current_cpc, new_cpc=rec.suggested_cpc,
        applied_at=clock.mock_now,
    ))
    campaign.current_cpc = rec.suggested_cpc
    rec.status = "applied"
    db.commit()
    return {"current_cpc": campaign.current_cpc}


@router.post("/ad-recommendations/{rec_id}/dismiss")
def dismiss_recommendation(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(AdRecommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "추천 없음")
    if rec.status != "pending":
        raise HTTPException(409, "대기 상태 추천만 무시 가능")
    rec.status = "dismissed"
    db.commit()
    return {"status": rec.status}
