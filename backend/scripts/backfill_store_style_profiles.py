"""DeepTwin 원칙 UI 플랜(2026-10-07)이 store_style_profile을 매장당 1행에서
(store_id, category)당 1행으로 재설계하면서, 마이그레이션 0004가 기존
store_style_profile 전체를 지웠다(옛 "매장 전체 통합" 행이 새 카테고리별
스키마에 정확히 대응하지 않아서). golden_examples는 그대로 보존되므로,
이 스크립트로 (store_id, category) 조합마다 refresh_store_style_profile을
한 번씩 돌려 한 번에 다시 채운다 — 안 돌리면 그 카테고리의 실제 답글
생성이 다음 저장 시점까지 _FALLBACK_STYLE_RULES(일반 사과문 원칙)로
떨어진다.

여러 번 실행해도 안전하다(refresh_store_style_profile 자체가 upsert).
golden_examples가 전혀 없는 (store_id, category) 조합은 refresh가 그냥
조용히 넘어간다(기존 동작, app/llm/style_profile.py 참고). 이 스크립트가
돌고 나면 재생성된 모든 카테고리가 처음 생성된 프로필이라
needs_confirmation=true로 뜬다 — 사장님이 배포 이후 한 번 전체를
훑어볼 자연스러운 계기가 된다, 의도된 동작이라 억지로 끄지 않는다."""

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.style_profile import refresh_store_style_profile
from app.models import GoldenExample


def backfill_store_style_profiles(db) -> dict:
    pairs = db.execute(
        select(GoldenExample.store_id, GoldenExample.category)
        .where(
            GoldenExample.is_manual.is_(True), GoldenExample.is_synthetic.is_(False),
            GoldenExample.needs_confirmation.is_(False),
        )
        .distinct()
    ).all()

    refreshed = 0
    failed = 0
    for store_id, category in pairs:
        try:
            refresh_store_style_profile(db, store_id, category)
            refreshed += 1
        except Exception as e:
            failed += 1
            print(f"실패 store_id={store_id} category={category}: {e}")

    return {"refreshed": refreshed, "failed": failed, "total": len(pairs)}


if __name__ == "__main__":
    session = SessionLocal()
    try:
        result = backfill_store_style_profiles(session)
        print(f"원칙 재생성: {result['refreshed']}건, 실패: {result['failed']}건 (대상 {result['total']}건)")
    finally:
        session.close()
