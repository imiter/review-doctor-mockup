"""골든 예시 검색 — category로 먼저 거르고, 그 안에서 새 리뷰와 의미적으로
가장 가까운 예시를 pgvector로 뽑는다(2026-08-26 추가, 이전엔 category 필터 +
최신순 LIMIT만 썼다). 카테고리당 예시가 몇 개 안 되면 매번 같은 2~3개가
반복 주입돼 답글이 정형화되는 문제가 실사용으로 확인됐다 — 카테고리는 정밀도
유지를 위해 그대로 두고, 그 안의 순위만 리뷰 내용 기반 유사도로 바꿨다.
진짜 예시(is_manual=true, is_synthetic=false)를 우선하고, 부족한 만큼만
순수 AI 생성 모범답안(is_synthetic=true)으로 보충하는 원칙은 그대로다.

golden_examples.embedding은 pgvector `vector(1024)` 컬럼이고, 순위는
`ORDER BY embedding <-> :query`로 Postgres가 직접 계산한다(SQLAlchemy에서는
Vector 타입의 `.cosine_distance()` 컴패리터). embedding이 아직 없는 행
(백필 전, 또는 Voyage 호출 실패로 저장 시점에 못 채운 행)은 유사도 순위
뒤에 최신순으로 붙는다 — 완전히 배제하지 않아 백필 전에도 기존과 동일하게
동작한다. Voyage 호출 자체가 실패하면(키 미설정, API 장애 등) 전체를
최신순 폴백으로 돌린다 — 답글 생성이 임베딩 API 가용성에 발목잡히면 안
된다.

pgvector는 SQLite에는 없는 Postgres 확장이라, 이 파일의 실제 순위 계산
(cosine_distance SQL 실행)은 in-memory SQLite를 쓰는 기본 유닛 테스트
스위트에서 검증할 수 없다 — 이 로직만 로컬 Postgres(pgvector 설치됨)를
쓰는 tests/test_llm_rag_pgvector.py에서 별도로 검증한다."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.llm.embedding import embed_document, embed_query
from app.models import GoldenExample, Review


def _query_ranked(db: Session, store_id: int, category: str, query_embedding: list[float] | None, limit: int, *, is_manual: bool | None = None, is_synthetic: bool | None = None) -> list[GoldenExample]:
    q = select(GoldenExample).where(
        GoldenExample.store_id == store_id,
        GoldenExample.category == category,
    )
    if is_manual is not None:
        q = q.where(GoldenExample.is_manual.is_(is_manual))
    if is_synthetic is not None:
        q = q.where(GoldenExample.is_synthetic.is_(is_synthetic))

    if query_embedding is not None:
        # embedding이 있는 행을 먼저(유사도 오름차순), 없는 행은 그 뒤에
        # 최신순으로 — 세 단계 정렬 키를 한 쿼리로 표현한다.
        q = q.order_by(
            GoldenExample.embedding.is_(None),
            GoldenExample.embedding.cosine_distance(query_embedding),
            GoldenExample.created_at.desc(),
        )
    else:
        q = q.order_by(GoldenExample.created_at.desc())

    return list(db.scalars(q.limit(limit)).all())


def fetch_golden_examples(db: Session, store_id: int, category: str, query_text: str, limit: int = 3) -> list[GoldenExample]:
    """골든 예시 조회. 진짜 예시를 우선하고, 부족한 만큼만 synthetic으로
    보충한다. 각 그룹 안에서는 query_text와 의미적으로 가까운 순서다."""
    try:
        query_embedding = embed_query(query_text)
    except Exception:
        query_embedding = None

    real = _query_ranked(db, store_id, category, query_embedding, limit, is_manual=True, is_synthetic=False)
    if len(real) >= limit:
        return real

    synthetic = _query_ranked(db, store_id, category, query_embedding, limit - len(real), is_synthetic=True)
    return real + synthetic


def compute_golden_example_embedding(review_text: str) -> list[float] | None:
    """골든 예시 생성 시점에 review_text를 벡터화한다. 실패해도(Voyage 키
    미설정, API 장애, 빈 문자열 등) None을 반환할 뿐 골든 예시 저장 자체를
    막지 않는다 — embedding이 없는 행은 위 폴백대로 최신순으로 뒤에 붙는다."""
    if not review_text.strip():
        return None
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
