"""골든 예시 검색 — category로 먼저 거르고, 그 안에서 새 리뷰와 의미적으로
가장 가까운 예시를 뽑는다(2026-08-26 추가, 이전엔 category 필터 + 최신순
LIMIT만 썼다). 카테고리당 예시가 몇 개 안 되면 매번 같은 2~3개가 반복
주입돼 답글이 정형화되는 문제가 실사용으로 확인됐다 — 카테고리는 정밀도
유지를 위해 그대로 두고, 그 안의 순위만 리뷰 내용 기반 유사도로 바꿨다.
진짜 예시(is_manual=true, is_synthetic=false)를 우선하고, 부족한 만큼만
순수 AI 생성 모범답안(is_synthetic=true)으로 보충하는 원칙은 그대로다.

Voyage 임베딩 벡터는 store당 골든 예시 수(많아야 수백 건)를 감안해 별도
벡터 인덱스(pgvector 등) 없이 Python에서 코사인 유사도로 직접 순위를
매긴다 — 이 규모에서는 선형 스캔이 충분히 빠르고, 벡터 확장 설치/운영
부담도 없앤다. embedding이 아직 없는 행(백필 전, 또는 Voyage 호출 실패로
저장 시점에 못 채운 행)은 유사도 순위 뒤에 최신순으로 붙는다 — 완전히
배제하지 않아 백필 전에도 기존과 동일하게 동작한다. Voyage 호출 자체가
실패하면(키 미설정, API 장애 등) 전체를 최신순 폴백으로 돌린다 — 답글
생성이 임베딩 API 가용성에 발목잡히면 안 된다."""

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.llm.embedding import embed_document, embed_query
from app.models import GoldenExample, Review


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rank_by_relevance(rows: list[GoldenExample], query_text: str, limit: int) -> list[GoldenExample]:
    if not rows:
        return []
    try:
        query_embedding = embed_query(query_text)
    except Exception:
        return sorted(rows, key=lambda r: r.created_at, reverse=True)[:limit]

    with_embedding = [r for r in rows if r.embedding]
    without_embedding = [r for r in rows if not r.embedding]
    with_embedding.sort(key=lambda r: _cosine_similarity(r.embedding, query_embedding), reverse=True)
    without_embedding.sort(key=lambda r: r.created_at, reverse=True)
    return (with_embedding + without_embedding)[:limit]


def fetch_golden_examples(db: Session, store_id: int, category: str, query_text: str, limit: int = 3) -> list[GoldenExample]:
    """골든 예시 조회. 진짜 예시를 우선하고, 부족한 만큼만 synthetic으로
    보충한다. 각 그룹 안에서는 query_text와 의미적으로 가까운 순서다."""
    real_candidates = list(db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
    ).all())
    real = _rank_by_relevance(real_candidates, query_text, limit)

    if len(real) >= limit:
        return real

    synthetic_candidates = list(db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_synthetic.is_(True),
        )
    ).all())
    synthetic = _rank_by_relevance(synthetic_candidates, query_text, limit - len(real))
    return real + synthetic


def compute_golden_example_embedding(review_text: str) -> list[float] | None:
    """골든 예시 생성 시점에 review_text를 벡터화한다. 실패해도(Voyage 키
    미설정, API 장애 등) None을 반환할 뿐 골든 예시 저장 자체를 막지
    않는다 — embedding이 없는 행은 위 폴백대로 최신순으로 뒤에 붙는다."""
    try:
        return embed_document(review_text)
    except Exception:
        return None


def compute_golden_example_embedding_background(golden_example_id: int) -> None:
    """FastAPI BackgroundTasks가 호출하는 얇은 래퍼 — Voyage API 호출
    지연으로 답글 저장 요청 자체가 느려지지 않도록 응답 이후에 실행한다.
    요청 스코프 세션은 이미 닫혀 있을 수 있어 자체 SessionLocal을 연다
    (app/llm/style_profile.py의 동일 패턴 참고)."""
    db = SessionLocal()
    try:
        example = db.get(GoldenExample, golden_example_id)
        if example is None:
            return
        example.embedding = compute_golden_example_embedding(example.review_text)
        db.commit()
    finally:
        db.close()


def count_recent_same_category(db: Session, store_id: int, category: str, days: int = 30) -> int:
    return db.scalar(
        select(func.count()).select_from(Review).where(
            Review.store_id == store_id,
            Review.category == category,
            Review.created_at >= datetime.now(timezone.utc) - timedelta(days=days),
        )
    )
