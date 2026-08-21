"""가게와 무관한 범용 "모범 답안" 시드 — 신규 가게가 아직 진짜
golden_example이 없는 카테고리에서도 즉시 어느 정도 참고할 예시를 갖도록
하는 콜드스타트 폴백이다. is_synthetic=true라 store_style_profile 추출
(refresh_store_style_profile)과 온보딩 커버리지 스캔
(find_uncovered_categories) 양쪽에서 전부 제외되고, fetch_golden_examples가
real 예시 부족분을 채울 때만 참고된다(설계 문서
2026-08-21-llm-rag-reply-onboarding-design.md 참고). 매장마다 카테고리당
1건씩만 있으면 되므로, 이미 있으면 건너뛴다(멱등) — 신규 매장이 생길
때마다 다시 실행해도 안전하다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import GoldenExample, Store

_SEED_EXAMPLES: dict[str, tuple[str, str]] = {
    "food_quality": (
        "닭이 너무 퍽퍽하고 식어서 왔어요. 맛이 예전같지 않네요.",
        "안녕하세요, 소중한 리뷰 남겨주셔서 감사합니다. 말씀해주신 맛과 관련된 부분, "
        "조리 과정을 다시 한번 꼼꼼히 점검하겠습니다. 기대하신 만큼 만족을 드리지 못해 "
        "죄송한 마음입니다. 다음에는 더 신경 써서 준비하겠습니다.",
    ),
    "delivery": (
        "주문한 지 1시간 넘게 걸려서 왔어요. 배달이 너무 늦습니다.",
        "안녕하세요, 배달 관련해서 불편을 드려 죄송합니다. 도착 시간과 포장 상태 모두 "
        "다시 한번 점검하고, 배달 파트너와도 상황을 공유하겠습니다. 소중한 시간 "
        "기다리시게 해드려 죄송하고, 앞으로 더 신경 쓰겠습니다.",
    ),
    "hygiene": (
        "포장에서 이상한 냄새가 나고 위생 상태가 걱정되네요.",
        "안녕하세요, 이런 불편을 드려 정말 죄송합니다. 말씀해주신 부분은 가볍게 넘기지 "
        "않고 바로 확인해서 원인을 찾아보겠습니다. 혹시 괜찮으시면 가게로 연락 한번 "
        "주시면 자세히 안내드리겠습니다. 다시 한번 죄송하고, 더 세심하게 신경 쓰겠습니다.",
    ),
    "service": (
        "전화로 문의했는데 응대가 너무 불친절했어요.",
        "안녕하세요, 응대 과정에서 불편을 드려 죄송합니다. 말씀해주신 내용 무겁게 "
        "받아들이고, 다시는 이런 일이 없도록 신경 쓰겠습니다. 소중한 의견 남겨주셔서 "
        "감사드리고, 더 나은 모습으로 찾아뵙겠습니다.",
    ),
    "price": (
        "양에 비해 가격이 좀 비싸다고 느껴져요.",
        "안녕하세요, 가격에 대해 아쉬운 마음 남겨주셔서 감사합니다. 저희도 재료와 "
        "품질을 유지하면서 최대한 합리적인 가격을 고민하고 있습니다. 말씀해주신 의견 "
        "참고해서 계속 더 나은 방법을 찾아보겠습니다.",
    ),
    "missing_or_wrong_item": (
        "주문한 메뉴가 아니라 다른 메뉴가 왔어요. 확인 좀 해주세요.",
        "안녕하세요, 주문하신 것과 다르게 받으셔서 많이 당황하셨겠습니다. 정말 "
        "죄송합니다. 포장 과정을 다시 한번 꼼꼼히 확인하도록 하겠습니다. 불편하신 "
        "부분 있으시면 가게로 연락 주시면 바로 도와드리겠습니다.",
    ),
}


def seed_synthetic_golden_examples(db: Session) -> int:
    inserted = 0
    store_ids = db.scalars(select(Store.id)).all()
    for store_id in store_ids:
        for category, (review_text, reply_text) in _SEED_EXAMPLES.items():
            already = db.scalar(
                select(GoldenExample).where(
                    GoldenExample.store_id == store_id,
                    GoldenExample.category == category,
                    GoldenExample.is_synthetic.is_(True),
                )
            )
            if already is not None:
                continue
            db.add(GoldenExample(
                store_id=store_id, category=category,
                review_text=review_text, reply_text=reply_text,
                is_manual=False, is_synthetic=True, source="synthetic",
                created_at=datetime.now(timezone.utc),
            ))
            inserted += 1
    db.commit()
    return inserted


if __name__ == "__main__":
    session = SessionLocal()
    try:
        count = seed_synthetic_golden_examples(session)
        print(f"{count}건의 범용 시드 예시를 추가했습니다.")
    finally:
        session.close()
