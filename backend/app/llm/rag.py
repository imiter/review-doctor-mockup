"""골든 예시 검색 — category로 먼저 거르고, 그 안에서 새 리뷰와 의미적으로
가장 가까운 예시를 pgvector로 뽑는다(2026-08-26 추가, 이전엔 category 필터 +
최신순 LIMIT만 썼다). 카테고리당 예시가 몇 개 안 되면 매번 같은 2~3개가
반복 주입돼 답글이 정형화되는 문제가 실사용으로 확인됐다 — 카테고리는 정밀도
유지를 위해 그대로 두고, 그 안의 순위만 리뷰 내용 기반 유사도로 바꿨다.
우선순위는 source 컬럼 기반 3단계다(2026-10-06 재작성). 그 전에는
is_manual/is_synthetic 플래그로만 묶었는데, 실제로 그 플래그는 source가
organic이든 onboarding이든 전부 is_manual=true/is_synthetic=false로 똑같이
들어가서 "사장님이 진짜 리뷰에 쓴 답글"과 "온보딩 가상 리뷰에 쓴 답글"을
구분할 방법이 아예 없었다 — 코드가 둘을 가르는 척하면서 실은 못 가르고
있었다. 그래서 묶는 기준을 source로 바꾸고, 그 안에서 "이 답글을 믿을 만
한가"를 고신뢰 신호로 한 번 더 갈랐다. 세 단계가 각각 무엇인지는
fetch_golden_examples의 docstring, 신호 3개는 _confidence_signal_exists
참고.
순수 AI 생성 모범답안(source='synthetic')으로 예시를 증강하는 메커니즘은
같은 날 완전히 제거했다 — 애초에 "명시적으로 채택하지 않음"으로 기록된
접근이라 새 3단계 설계에 들어갈 자리가 없다.

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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db import SessionLocal
from app.llm.embedding import embed_document, embed_query
from app.models import GoldenExample, Review

_CONSISTENCY_MIN_BASELINE = 3
_CONSISTENCY_DISTANCE_THRESHOLD = 0.5

# 사람이 직접 쓴 답글로 간주하는 소스 — 사장님이 앱에서 쓰거나 고친 답글
# (organic), 배민에 직접 단 답글(organic_direct), 연동 전 기존 답글 백필
# (backfill). 셋을 소스 종류로 더 나누지 않고 아래 신호로만 1·2단계를
# 가른다(어느 쪽이든 "진짜 리뷰에 대한 진짜 답글"이라는 점은 같다).
_HUMAN_SOURCES = ("organic", "organic_direct", "backfill")
# 그 리뷰가 들어온 시점에 이 매장 누적 리뷰가 이 건수 이하면 "초창기 리뷰"로
# 보고 고신뢰 신호로 센다 — 리뷰가 몇 개 없을 때 사장님이 직접 공들여 쓴
# 답글일 확률이 높다.
_EARLY_REVIEW_COUNT = 3


def _confidence_signal_exists(store_id: int):
    """고신뢰 신호 — GoldenExample에 연결된 리뷰(source_review_id)가 셋 중
    하나라도 만족하면 참이다: (a) 그 리뷰가 들어온 시점의 누적 리뷰가
    _EARLY_REVIEW_COUNT건 이하, (b) 불만 카테고리(no_issue 아님), (c)
    별점-내용 불일치(sentiment_conflict). 연결된 리뷰가 아예 없는
    (source_review_id IS NULL) 예시는 EXISTS가 자연히 거짓이 되어 "신호
    없음"으로 떨어진다 — 연결만으로 1단계로 올라가면 신호 판정이
    무의미해지므로 의도된 동작이다.

    (a)는 "지금 리뷰 수"나 "통산 리뷰 수"가 아니라 그 리뷰 시점 기준이라,
    reviews를 한 번 더 세는 상관 서브쿼리가 필요하다 — 행마다 파이썬에서
    세면 N+1이 되므로 SQL 한 문장 안에 넣는다."""
    linked = aliased(Review)
    reviews_at_that_time = (
        select(func.count())
        .select_from(Review)
        .where(
            Review.store_id == store_id,
            Review.created_at <= linked.created_at,
        )
        .correlate(linked)
        .scalar_subquery()
    )
    return (
        select(linked.id)
        .where(
            linked.id == GoldenExample.source_review_id,
            or_(
                reviews_at_that_time <= _EARLY_REVIEW_COUNT,
                linked.category != "no_issue",
                linked.sentiment_conflict.is_(True),
            ),
        )
        .correlate(GoldenExample)
        .exists()
    )


def _query_ranked(db: Session, store_id: int, category: str, query_embedding: list[float] | None, limit: int, *, sources: tuple[str, ...], confidence: bool | None = None) -> list[GoldenExample]:
    """한 단계(sources + 신뢰 조건)에 해당하는 예시를 유사도 순으로 limit개
    까지 가져온다.

    confidence=True  → 1단계: 고신뢰 신호가 있고 needs_confirmation=False인 것만.
    confidence=False → 2단계: 그 나머지 전부(신호가 없는 것, 그리고 신호가
                        있어도 needs_confirmation=True라 1단계에서 빠진 것) —
                        1단계 조건의 정확한 여집합이라 겹치거나 빠지는 행이 없다.
    confidence=None  → 신뢰 조건을 전혀 안 따진다(3단계, onboarding)."""
    q = select(GoldenExample).where(
        GoldenExample.store_id == store_id,
        GoldenExample.category == category,
        GoldenExample.source.in_(sources),
    )
    if confidence is not None:
        signal = _confidence_signal_exists(store_id)
        tier1_condition = signal & GoldenExample.needs_confirmation.is_(False)
        q = q.where(tier1_condition if confidence else ~tier1_condition)

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
    """골든 예시 조회, source 기반 3단계 우선순위:

    1단계 — 사람이 직접 쓴 답글(_HUMAN_SOURCES) 중 고신뢰 신호가 있는 것,
            단 needs_confirmation=True(경로 C에서 말투 이상치로 찍힌 미확인
            행, promote_direct_reply_to_golden_example 참고)는 제외한다 —
            시스템이 스스로 "진짜 사장님 말투인지 의심됨"으로 표시한 답글을
            그 반대인 "가장 신뢰할 수 있는" 단계에 올릴 수는 없다(2026-10-06).
    2단계 — 사람이 직접 쓴 답글 중 신호가 없는 것, 또는 신호가 있어도
            needs_confirmation=True라 1단계에서 제외된 것. 데이터 자체는
            여전히 유효하므로 버리지 않고 이 단계로 내린다 — 불확실한
            데이터는 플래그만 세우고 버리지 않는다는 이 프로젝트의 원칙과
            같다(CLAUDE.md).
    3단계 — onboarding(가상 리뷰에 쓴 답글). 진짜 리뷰에 대한 답글이 모자랄
            때만 쓰는 순수 폴백이라 맨 뒤다.

    위 단계부터 차례로 limit을 채우고, 각 단계 안에서는 query_text와
    의미적으로 가까운 순서다(embedding 없으면 최신순)."""
    try:
        query_embedding = embed_query(query_text)
    except Exception:
        query_embedding = None

    picked = _query_ranked(
        db, store_id, category, query_embedding, limit,
        sources=_HUMAN_SOURCES, confidence=True,
    )
    if len(picked) >= limit:
        return picked

    picked += _query_ranked(
        db, store_id, category, query_embedding, limit - len(picked),
        sources=_HUMAN_SOURCES, confidence=False,
    )
    if len(picked) >= limit:
        return picked

    return picked + _query_ranked(
        db, store_id, category, query_embedding, limit - len(picked),
        sources=("onboarding",),
    )


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


def check_voice_consistency(db: Session, store_id: int, category: str, candidate_embedding: list[float]) -> bool | None:
    """경로 C(배민 직접 답글) 후보가 이미 신뢰할 수 있는 예시(organic/
    backfill) 클러스터와 말투가 일관되는지 본다 — "AI가 썼는지"가 아니라
    "이 가게 말투에 맞는지"를 묻는 질문으로 바꾼 것. 비교할 기준(organic/
    backfill, embedding 있는 것)이 _CONSISTENCY_MIN_BASELINE개 미만이면
    판단 불가로 None을 반환한다(신생 매장은 이 체크를 건너뛴다)."""
    baseline_q = select(GoldenExample.id).where(
        GoldenExample.store_id == store_id,
        GoldenExample.category == category,
        GoldenExample.source.in_(("organic", "backfill")),
        GoldenExample.embedding.is_not(None),
    )
    baseline_count = len(db.scalars(baseline_q).all())
    if baseline_count < _CONSISTENCY_MIN_BASELINE:
        return None

    nearest = db.scalars(
        select(GoldenExample.embedding.cosine_distance(candidate_embedding))
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.source.in_(("organic", "backfill")),
            GoldenExample.embedding.is_not(None),
        )
        .order_by(GoldenExample.embedding.cosine_distance(candidate_embedding))
        .limit(3)
    ).all()
    avg_distance = sum(nearest) / len(nearest)
    return avg_distance <= _CONSISTENCY_DISTANCE_THRESHOLD


def promote_direct_reply_to_golden_example(db: Session, review: Review, reply_id: int, reply_text: str) -> GoldenExample:
    """review_sync.py가 extract_owner_reply로 감지한, 배민에 이미 달려있던
    사장님 답글을 golden_example로 승격한다(경로 C). 앱을 거치지 않은
    답글이라 진짜 사장님 말투인지 보장이 없다 — 이상치로 판정돼도 저장
    자체는 막지 않는다. needs_confirmation만 세워서 나중에 사장님 확인
    UI(이 작업 범위 밖)가 쓸 수 있게 한다."""
    embedding = compute_golden_example_embedding(review.content)
    needs_confirmation = False
    if embedding is not None:
        consistent = check_voice_consistency(db, review.store_id, review.category, embedding)
        needs_confirmation = consistent is False

    example = GoldenExample(
        store_id=review.store_id, category=review.category,
        review_text=review.content, reply_text=reply_text,
        is_manual=True, is_synthetic=False, source="organic_direct",
        source_review_id=review.id, source_reply_id=reply_id,
        embedding=embedding, needs_confirmation=needs_confirmation,
        created_at=datetime.now(timezone.utc),
    )
    db.add(example)
    return example
