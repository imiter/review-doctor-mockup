"""말투 일관성 체크(app/llm/rag.py의 check_voice_consistency)가 쓰는
golden_examples.reply_embedding은 2026-10-06에 추가된 컬럼이라, 그 전에
이미 쌓여있던 행은 전부 비어있다 — 이 스크립트로 한 번에 채운다.
reply_text를 Voyage AI로 벡터화해 저장하며, 배치로 묶어 호출 수를 줄인다.
여러 번 실행해도 안전하다(reply_embedding IS NULL인 행만 대상으로 삼는다 —
이미 채운 행은 재계산하지 않는다).

review_text용 백필(backfill_golden_example_embeddings.py)과 완전히 같은
패턴이고, 벡터화 대상 컬럼만 reply_text로 다르다. reply_text가 빈 문자열인
행은 건너뛴다 — Voyage가 배치 안에 빈 문자열이 하나라도 있으면 요청 전체를
400으로 거부하고(실측 확인, 2026-08-26), 어차피 벡터화할 말투도 없다.
reply_embedding이 NULL로 남은 행은 일관성 체크의 기준 집합에서 빠질 뿐
다른 동작에는 영향이 없다."""

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.embedding import embed_documents
from app.models import GoldenExample

_BATCH_SIZE = 100


def backfill_golden_example_reply_embeddings(db) -> dict:
    all_rows = db.scalars(
        select(GoldenExample).where(GoldenExample.reply_embedding.is_(None))
    ).all()
    rows = [r for r in all_rows if r.reply_text.strip()]
    skipped_empty = len(all_rows) - len(rows)

    updated = 0
    failed = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            embeddings = embed_documents([r.reply_text for r in batch])
        except Exception as e:
            failed += len(batch)
            print(f"배치 실패({i}~{i + len(batch)}): {e}")
            continue
        for row, embedding in zip(batch, embeddings):
            row.reply_embedding = embedding
            updated += 1
        db.commit()

    return {"updated": updated, "failed": failed, "skipped_empty": skipped_empty, "total": len(all_rows)}


if __name__ == "__main__":
    session = SessionLocal()
    try:
        result = backfill_golden_example_reply_embeddings(session)
        print(
            f"답글 임베딩 채움: {result['updated']}건, 실패: {result['failed']}건, "
            f"내용 없어 건너뜀: {result['skipped_empty']}건 (대상 {result['total']}건)"
        )
    finally:
        session.close()
