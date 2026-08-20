"""골든 예시 검색 — 벡터 검색이 아니라 category 필터 + 최신순 LIMIT만
쓴다. 진짜 예시(is_manual=true, is_synthetic=false)를 우선하고, 부족한
만큼만 순수 AI 생성 모범답안(is_synthetic=true)으로 보충한다."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GoldenExample, Review


def fetch_golden_examples(db: Session, store_id: int, category: str, limit: int = 3) -> list[GoldenExample]:
    """골든 예시 조회. 진짜 예시를 우선하고, 부족한 만큼만 synthetic으로 보충한다."""
    real = list(db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit)
    ).all())

    # 진짜 예시만으로 limit을 채웠으면 그것만 반환
    if len(real) >= limit:
        return real

    # 부족한 만큼 순수 AI 생성 예시(is_synthetic=True)로 보충
    synthetic = list(db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_synthetic.is_(True),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit - len(real))
    ).all())
    return real + synthetic


def count_recent_same_category(db: Session, store_id: int, category: str, days: int = 30) -> int:
    return db.scalar(
        select(func.count()).select_from(Review).where(
            Review.store_id == store_id,
            Review.category == category,
            Review.created_at >= datetime.now(timezone.utc) - timedelta(days=days),
        )
    )
