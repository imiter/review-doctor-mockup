from datetime import datetime, timezone

from app.models import ReviewSyncJob


def test_review_sync_job_triggered_by_defaults_to_manual(db_session, seeded_user, platforms):
    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()

    row = db_session.query(ReviewSyncJob).filter_by(id=job.id).one()
    assert row.triggered_by == "manual"


def test_review_sync_job_triggered_by_accepts_scheduled(db_session, seeded_user, platforms):
    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc), triggered_by="scheduled",
    )
    db_session.add(job)
    db_session.commit()

    row = db_session.query(ReviewSyncJob).filter_by(id=job.id).one()
    assert row.triggered_by == "scheduled"
