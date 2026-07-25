"""가게별 답글 설정. '답글 규칙 설정'(자동 답글 조건)과 '답글 스타일 설정'(말투·홍보문구)
화면이 공유하는 하나의 reply_settings 레코드를 읽고 쓴다.

자동 답글은 Mock이다 — auto_reply_enabled를 켜도 실제로 답글이 자동 등록되지 않는다.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_default_store_id
from app.db import get_db
from app.models import ReplySetting, User

router = APIRouter(tags=["reply-settings"])


def _row(rs: ReplySetting) -> dict:
    return {
        "store_id": rs.store_id,
        "style_id": rs.style_id,
        "promo_text": rs.promo_text,
        "include_nickname": rs.include_nickname,
        "include_menu": rs.include_menu,
        "include_store_name": rs.include_store_name,
        "promo_on_negative": rs.promo_on_negative,
        "auto_reply_enabled": rs.auto_reply_enabled,
        "auto_reply_min_rating": rs.auto_reply_min_rating,
    }


@router.get("/reply-settings")
def get_reply_settings(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == sid))
    if rs is None:
        raise HTTPException(404, "답글 설정이 없습니다")
    return _row(rs)


class ReplySettingsUpdate(BaseModel):
    style_id: int | None = None
    promo_text: str | None = None
    include_nickname: bool | None = None
    include_menu: bool | None = None
    include_store_name: bool | None = None
    promo_on_negative: bool | None = None
    auto_reply_enabled: bool | None = None
    auto_reply_min_rating: int | None = None


@router.put("/reply-settings")
def update_reply_settings(
    body: ReplySettingsUpdate,
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    rs = db.scalar(select(ReplySetting).where(ReplySetting.store_id == sid))
    if rs is None:
        raise HTTPException(404, "답글 설정이 없습니다")

    if body.auto_reply_min_rating is not None and not (1 <= body.auto_reply_min_rating <= 5):
        raise HTTPException(422, "auto_reply_min_rating은 1~5 사이여야 합니다")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rs, field, value)
    db.commit()
    return _row(rs)
