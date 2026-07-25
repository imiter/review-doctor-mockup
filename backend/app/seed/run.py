import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import (
    AdCampaign, AdRankSnapshot, MockClock, Order, OrderDeduction, Owner,
    Platform, ReplyStyle, ReplyTemplate, Review, ReviewReply, Settlement,
    Store, StorePlatform,
)

BASE_NOW = datetime(2026, 7, 25, 9, 0)

PLATFORMS = [
    ("baemin", "배달의민족", 0.068),
    ("coupang_eats", "쿠팡이츠", 0.098),
    ("yogiyo", "요기요", 0.125),
]

TEMPLATES = {
    "친근함": {
        "high": "{reviewer_name}님~ 맛있게 드셨다니 저희가 더 행복해요! 다음에도 따끈하게 준비해둘게요 :)",
        "mid": "{reviewer_name}님, 솔직한 후기 감사해요! 다음엔 더 만족하실 수 있게 신경 쓸게요~",
        "low": "{reviewer_name}님, 불편을 드려 정말 죄송해요ㅠㅠ 말씀 주신 부분 바로 개선하겠습니다. 한 번만 더 기회 주세요!",
    },
    "장난꾸러기": {
        "high": "{reviewer_name}님!! 별 다섯 개 감사링~ 사장님 오늘 어깨 승천했습니다ㅋㅋ 또 오세용!",
        "mid": "{reviewer_name}님 아쉬운 부분이 있었군요! 사장님이 주방에 특훈 지시했습니다. 다음엔 꼭 만족시켜드릴게요!",
        "low": "{reviewer_name}님... 사장님 지금 반성의 정자세 중입니다. 죄송합니다! 다음엔 실망 안 시켜드릴게요!",
    },
    "정중함": {
        "high": "{reviewer_name}님, 소중한 리뷰 감사드립니다. 앞으로도 변함없는 맛과 서비스로 보답하겠습니다.",
        "mid": "{reviewer_name}님, 귀한 의견 감사드립니다. 말씀하신 부분을 검토하여 개선하겠습니다.",
        "low": "{reviewer_name}님, 기대에 미치지 못해 진심으로 사과드립니다. 지적해주신 사항은 즉시 개선하겠습니다.",
    },
}

REVIEW_SAMPLES = {
    "high": ["진짜 맛있어요 재주문 의사 100%", "바삭하고 양도 많아요. 최고!", "배달도 빠르고 친절해요"],
    "mid": ["맛은 있는데 배달이 좀 늦었어요", "무난해요. 가끔 시켜먹기 좋아요"],
    "low": ["식어서 왔어요. 실망입니다", "주문한 거랑 다른 게 왔어요"],
}

REVIEWERS = ["먹보", "치킨러버", "동네주민", "야식왕", "리뷰요정", "단골손님", "익명", "맛잘알"]


def band_of(rating: int) -> str:
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


def deductions_for(platform_code: str, item_amount: int, delivery_tip: int, rng: random.Random):
    gross = item_amount + delivery_tip
    if platform_code == "baemin":
        d = [("platform_commission", round(item_amount * 0.068)),
             ("payment_fee", round(gross * 0.03)),
             ("delivery_fee", 3300)]
    elif platform_code == "coupang_eats":
        d = [("platform_commission", round(item_amount * 0.098)),
             ("payment_fee", round(gross * 0.03)),
             ("delivery_fee", 2900)]
    else:  # yogiyo
        d = [("platform_commission", round(item_amount * 0.125)),
             ("payment_fee", round(gross * 0.03))]
    if rng.random() < 0.10:
        d.append(("ad_fee", rng.randrange(200, 601, 50)))
    return d


def seed_all(session: Session) -> None:
    rng = random.Random(42)

    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())

    owner = Owner(name="김사장", phone="010-1234-5678")
    store1 = Store(owner=owner, name="우리치킨 1호점", address="서울시 관악구 1")
    store2 = Store(owner=owner, name="우리치킨 2호점", address="서울시 동작구 2")
    platforms = {c: Platform(code=c, name=n, default_commission_rate=r) for c, n, r in PLATFORMS}
    session.add_all([owner, store1, store2, *platforms.values()])
    session.flush()

    sps = [
        StorePlatform(store=store1, platform=platforms["baemin"], platform_store_name="우리치킨-관악점"),
        StorePlatform(store=store1, platform=platforms["coupang_eats"], platform_store_name="우리치킨 관악"),
        StorePlatform(store=store1, platform=platforms["yogiyo"], platform_store_name="우리치킨(관악)"),
        StorePlatform(store=store2, platform=platforms["baemin"], platform_store_name="우리치킨-동작점"),
    ]
    session.add_all(sps)
    session.flush()

    # ---- 주문 60일치 ----
    orders_by_sp: dict[int, list[Order]] = {sp.id: [] for sp in sps}
    seq = 0
    for day_offset in range(60, 0, -1):
        day = BASE_NOW.date() - timedelta(days=day_offset)
        for sp in sps:
            for _ in range(rng.randint(1, 3)):
                seq += 1
                ordered_at = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=rng.randint(11, 21), minutes=rng.randint(0, 59)
                )
                order = Order(
                    store_platform_id=sp.id,
                    order_no=f"{sp.platform.code[:2].upper()}{day.strftime('%Y%m%d')}-{seq:04d}",
                    ordered_at=ordered_at,
                    item_amount=rng.randrange(15000, 36000, 1000),
                    delivery_tip=rng.choice([0, 1000, 2000, 3000]),
                    status="completed",
                )
                session.add(order)
                session.flush()
                for dtype, amount in deductions_for(sp.platform.code, order.item_amount, order.delivery_tip, rng):
                    session.add(OrderDeduction(order_id=order.id, type=dtype, amount=amount))
                orders_by_sp[sp.id].append(order)

    session.flush()  # Ensure all order deductions are persisted before settlement invariant calculation

    # ---- 주 단위 정산 (월~일, 입금 = 종료 +3일) ----
    for sp in sps:
        by_week: dict[date, list[Order]] = {}
        for order in orders_by_sp[sp.id]:
            monday = order.ordered_at.date() - timedelta(days=order.ordered_at.weekday())
            by_week.setdefault(monday, []).append(order)
        for monday, orders in sorted(by_week.items()):
            gross = sum(o.item_amount + o.delivery_tip for o in orders)
            ded = sum(d.amount for o in orders for d in o.deductions)
            payout = monday + timedelta(days=9)  # 일요일 종료 +3일 = 다음주 수요일
            settlement = Settlement(
                store_platform_id=sp.id,
                period_start=monday, period_end=monday + timedelta(days=6),
                payout_date=payout,
                total_gross=gross, total_deductions=ded, net_payout=gross - ded,
                status="paid" if payout < BASE_NOW.date() else "scheduled",
            )
            session.add(settlement)
            session.flush()
            for o in orders:
                o.settlement_id = settlement.id

    # ---- 답글 스타일/템플릿 ----
    styles = {}
    descs = {"친근함": "따뜻하고 다정한 말투", "장난꾸러기": "유쾌하고 장난스러운 말투", "정중함": "격식 있는 말투"}
    for name, bands in TEMPLATES.items():
        style = ReplyStyle(name=name, description=descs[name])
        session.add(style)
        session.flush()
        styles[name] = style
        for rating_band, text in bands.items():
            session.add(ReplyTemplate(style_id=style.id, rating_band=rating_band, template_text=text))

    # ---- 리뷰 40건, 절반 답글 완료 ----
    rating_pool = [5] * 22 + [4] * 8 + [3] * 4 + [2] * 3 + [1] * 3
    rng.shuffle(rating_pool)
    for i, rating in enumerate(rating_pool):
        b = band_of(rating)
        reviewer = rng.choice(REVIEWERS)
        review = Review(
            store_platform_id=rng.choice(sps).id,
            rating=rating,
            content=rng.choice(REVIEW_SAMPLES[b]),
            reviewer_name=reviewer,
            has_photo=rng.random() < 0.3,
            status="answered" if i < 20 else "unanswered",
            created_at=BASE_NOW - timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 12)),
        )
        session.add(review)
        session.flush()
        if review.status == "answered":
            style = rng.choice(list(styles.values()))
            text = TEMPLATES[style.name][b].replace("{reviewer_name}", reviewer)
            session.add(ReviewReply(
                review_id=review.id, style_id=style.id, content=text,
                created_at=review.created_at + timedelta(hours=rng.randint(1, 24)),
            ))

    # ---- 광고: 캠페인 2개, 10분 간격 스냅샷 30개 ----
    c1 = AdCampaign(store_platform_id=sps[0].id, category="치킨", current_cpc=400, target_rank=3, status="active")
    c2 = AdCampaign(store_platform_id=sps[1].id, category="치킨", current_cpc=300, target_rank=5, status="active")
    session.add_all([c1, c2])
    session.flush()
    for i in range(30):
        at = BASE_NOW + timedelta(minutes=10 * i)
        if i < 10:
            rank1, comp1 = 3, 390
        elif i < 15:
            rank1, comp1 = min(3 + (i - 9), 7), 650  # 3위→7위 밀림 구간
        else:
            rank1, comp1 = 7, 650
        session.add(AdRankSnapshot(campaign_id=c1.id, snapshot_at=at, my_rank=rank1, competitor_est_cpc=comp1))
        session.add(AdRankSnapshot(campaign_id=c2.id, snapshot_at=at, my_rank=rng.choice([1, 2]), competitor_est_cpc=280))

    session.add(MockClock(id=1, mock_now=BASE_NOW))
    session.commit()


if __name__ == "__main__":
    Base.metadata.create_all(engine)  # alembic 미적용 환경 대비 no-op 안전장치
    with SessionLocal() as session:
        seed_all(session)
    print("seed 완료")
