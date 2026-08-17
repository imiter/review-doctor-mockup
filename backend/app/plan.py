"""구독 플랜 판정과 날짜 계산 — 순수 로직 + 답글 카운트 조회.

만료/한도 리셋은 전부 KST(Asia/Seoul) 자정 기준이다. 서버가 UTC로 돌아가도
(Railway 배포 환경) 날짜 경계가 어긋나면 안 되므로, 이 모듈 밖에서는
date.today()를 직접 쓰지 말고 kst_today()를 통해서만 "오늘"을 구한다.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Review, ReviewReply, Store, Subscription, User

KST = ZoneInfo("Asia/Seoul")
PRO_MONTHLY_PRICE = 19900


def kst_today() -> date:
    return datetime.now(KST).date()


def add_one_month(d: date) -> date:
    """달력월 기준 +1개월. 월말은 다음 달 마지막 날로 클램핑한다(1/31 + 1개월 = 2/28)."""
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def effective_plan(sub: Subscription | None) -> str:
    """만료를 조회 시점에 lazy 판정한다(별도 배치/크론 없음)."""
    if sub is None or sub.plan != "pro":
        return "basic"
    if sub.expires_at is not None and sub.expires_at >= kst_today():
        return "pro"
    return "basic"


def _kst_today_range() -> tuple[datetime, datetime]:
    start_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_kst.astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def replies_used_today(user: User, db: Session) -> int:
    """오늘 사용한 "답글 생성" 건수. `ReviewReply.reply_type`에는 'ai_draft'(AI 초안 생성),
    'final'(최종 답글 저장), 'secondary'(2차 답글) 세 종류가 있는데, 프론트 배지/가격
    카드/약관에서 말하는 "답글 생성 하루 N건"은 AI 답글 생성만 의미한다 — final/secondary까지
    세면 리뷰 하나 처리에 한도가 여러 번 깎여 실질 한도가 줄어드는 버그가 된다."""
    start, end = _kst_today_range()
    count = db.scalar(
        select(func.count(ReviewReply.id))
        .join(Review, ReviewReply.review_id == Review.id)
        .join(Store, Review.store_id == Store.id)
        .where(
            Store.user_id == user.id,
            ReviewReply.reply_type == "ai_draft",
            ReviewReply.created_at >= start,
            ReviewReply.created_at < end,
        )
    )
    return count or 0
