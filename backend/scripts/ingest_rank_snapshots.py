"""crawler/output/results.csv(실측 반경별 순위)를 ad_rank_snapshots에 적재한다.

crawler/는 사이트(FastAPI) 프로세스와 별개로 실기기에서 배민 앱을 조작해
순위를 실측하는 독립 배치 도구다. 이 스크립트는 그 결과 CSV를 읽어서
해당 캠페인의 ad_rank_snapshots에 distance_km와 함께 저장한다 — 사이트는
요청 시점에 실시간으로 크롤링하지 않고, 이렇게 적재된 값을 조회만 한다.

사용법 (backend/ 에서 실행, backend venv 사용):
    python scripts/ingest_rank_snapshots.py --campaign-id 1
    python scripts/ingest_rank_snapshots.py --campaign-id 1 --csv ../crawler/output/results.csv
"""

import argparse
import csv
import datetime
import pathlib
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_KST = zoneinfo.ZoneInfo("Asia/Seoul")

from app.db import SessionLocal  # noqa: E402
from app.models import AdCampaign, AdRankSnapshot  # noqa: E402

DEFAULT_CSV = pathlib.Path(__file__).resolve().parent.parent.parent / "crawler" / "output" / "results.csv"


def _parse_rank(raw: str) -> int | None:
    """숫자면 순위, 'NOT_FOUND'/'PARSE_ERROR'/'NAV_ERROR: ...'처럼 못 찾은 경우는 None."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def ingest(csv_path: pathlib.Path, campaign_id: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        campaign = db.get(AdCampaign, campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign_id={campaign_id}에 해당하는 ad_campaigns 행이 없습니다")

        inserted, skipped = 0, 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rank = _parse_rank(row["rank"])
                if rank is None:
                    print(f"  스킵 ({row['point_label']}): rank={row['rank']!r} (못 찾음/에러)")
                    skipped += 1
                    continue

                snapshot = AdRankSnapshot(
                    campaign_id=campaign.id,
                    snapshot_at=datetime.datetime.fromisoformat(row["timestamp"]).replace(tzinfo=_KST),
                    current_rank=rank,
                    status="rank_dropped" if rank > campaign.target_rank else "normal",
                    distance_km=row["distance_km"] or None,
                    point_label=row["point_label"],
                    total_scanned=int(row["total_scanned"]),
                    ads_above=int(row["ads_above"]),
                )
                db.add(snapshot)
                inserted += 1

        db.commit()
        return inserted, skipped
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", type=int, required=True, help="적재 대상 ad_campaigns.id")
    parser.add_argument("--csv", type=pathlib.Path, default=DEFAULT_CSV, help="crawler 결과 CSV 경로")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV 파일을 찾을 수 없습니다: {args.csv}")

    inserted, skipped = ingest(args.csv, args.campaign_id)
    print(f"완료: {inserted}개 적재, {skipped}개 스킵 (campaign_id={args.campaign_id})")


if __name__ == "__main__":
    main()
