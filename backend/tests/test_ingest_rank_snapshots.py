import csv
import pathlib

from sqlalchemy.orm import sessionmaker

import scripts.ingest_rank_snapshots as ingest_module
from app.models import AdCampaign, AdRankSnapshot


def _write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    fieldnames = ["timestamp", "rank", "distance_km", "point_label", "total_scanned", "ads_above"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_ingest_records_bid_at_snapshot_from_campaign_current_cpc(db_session, seeded_user, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "SessionLocal", sessionmaker(bind=db_session.get_bind(), autoflush=False))
    campaign = AdCampaign(
        store_id=seeded_user["store"].id, category="치킨", current_cpc=125, target_rank=3,
        status="active", shop_no="14804318",
    )
    db_session.add(campaign)
    db_session.commit()

    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-08-18T09:00:00", "rank": "5", "distance_km": "0",
         "point_label": "0km", "total_scanned": "20", "ads_above": "2"},
    ])

    inserted, skipped = ingest_module.ingest(csv_path, campaign.id)

    assert (inserted, skipped) == (1, 0)
    snapshot = db_session.query(AdRankSnapshot).filter_by(campaign_id=campaign.id).one()
    assert snapshot.bid_at_snapshot == 125


def test_ingest_skips_unparseable_rank_without_bid_at_snapshot(db_session, seeded_user, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "SessionLocal", sessionmaker(bind=db_session.get_bind(), autoflush=False))
    campaign = AdCampaign(
        store_id=seeded_user["store"].id, category="치킨", current_cpc=125, target_rank=3,
        status="active", shop_no="14804318",
    )
    db_session.add(campaign)
    db_session.commit()

    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-08-18T09:00:00", "rank": "NOT_FOUND", "distance_km": "0",
         "point_label": "0km", "total_scanned": "20", "ads_above": "2"},
    ])

    inserted, skipped = ingest_module.ingest(csv_path, campaign.id)

    assert (inserted, skipped) == (0, 1)
    assert db_session.query(AdRankSnapshot).filter_by(campaign_id=campaign.id).count() == 0
