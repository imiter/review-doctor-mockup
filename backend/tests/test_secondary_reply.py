from datetime import datetime, timezone

from app.models import Order, Review, ReviewReply


def make_answered_review(db_session, store, platform):
    order = Order(
        store_id=store.id, platform_id=platform.id, order_no="SEC-1",
        ordered_at=datetime.now(timezone.utc), menu_summary="치킨", order_type="delivery", amount=20000,
    )
    db_session.add(order)
    db_session.flush()
    review = Review(
        order_id=order.id, store_id=store.id, platform_id=platform.id, menu_summary="치킨",
        rating=5, content="좋아요", customer_nickname="단골",
        customer_order_count=3, status="answered", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewReply(
        review_id=review.id, reply_type="final", style_id=None,
        content="감사합니다", created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    return review


def make_unanswered_review(db_session, store, platform):
    order = Order(
        store_id=store.id, platform_id=platform.id, order_no="SEC-2",
        ordered_at=datetime.now(timezone.utc), menu_summary="치킨", order_type="delivery", amount=20000,
    )
    db_session.add(order)
    db_session.flush()
    review = Review(
        order_id=order.id, store_id=store.id, platform_id=platform.id, menu_summary="치킨",
        rating=5, content="좋아요", customer_nickname="단골2",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()
    return review


def test_secondary_reply_added_to_answered_review(client, db_session, seeded_user, platforms, auth_headers):
    review = make_answered_review(db_session, seeded_user["store"], platforms["baemin"])

    res = client.post(f"/reviews/{review.id}/secondary-reply", json={"content": "추가로 안내드립니다!"}, headers=auth_headers)
    assert res.status_code == 200

    listed = client.get("/reviews?status=answered", headers=auth_headers).json()
    assert len(listed[0]["secondary_replies"]) == 1
    assert listed[0]["secondary_replies"][0]["content"] == "추가로 안내드립니다!"
    assert listed[0]["final_reply"]["content"] == "감사합니다"  # 1차 답글은 그대로 유지


def test_secondary_reply_rejected_before_first_reply(client, db_session, seeded_user, platforms, auth_headers):
    review = make_unanswered_review(db_session, seeded_user["store"], platforms["baemin"])
    res = client.post(f"/reviews/{review.id}/secondary-reply", json={"content": "안내"}, headers=auth_headers)
    assert res.status_code == 409


def test_secondary_reply_allows_multiple(client, db_session, seeded_user, platforms, auth_headers):
    review = make_answered_review(db_session, seeded_user["store"], platforms["baemin"])
    client.post(f"/reviews/{review.id}/secondary-reply", json={"content": "첫 번째 추가 답글"}, headers=auth_headers)
    client.post(f"/reviews/{review.id}/secondary-reply", json={"content": "두 번째 추가 답글"}, headers=auth_headers)

    listed = client.get("/reviews?status=answered", headers=auth_headers).json()
    contents = [r["content"] for r in listed[0]["secondary_replies"]]
    assert contents == ["첫 번째 추가 답글", "두 번째 추가 답글"]  # 등록 순서 유지
