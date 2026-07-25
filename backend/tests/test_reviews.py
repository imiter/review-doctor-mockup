from datetime import datetime, timezone

from app.models import Order, Review


def make_review(db_session, store, platforms, rating, content="테스트 리뷰"):
    order = Order(
        store_id=store.id, platform_id=platforms["baemin"].id, order_no=f"T-{rating}-{content[:3]}",
        ordered_at=datetime.now(timezone.utc), menu_summary="양념치킨", order_type="delivery", amount=20000,
    )
    db_session.add(order)
    db_session.flush()
    review = Review(
        order_id=order.id, rating=rating, content=content, customer_nickname="먹보",
        customer_order_count=2, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()
    return review


def test_generate_reply_fills_template_and_sets_pending(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    review = make_review(db_session, seeded_user["store"], platforms, rating=5)

    res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert "먹보" in body["content"]
    assert "양념치킨" in body["content"]
    assert "치킨대장" in body["content"]
    assert "{nickname}" not in body["content"]  # 플레이스홀더가 전부 치환됐는지

    listed = client.get("/reviews?status=pending", headers=auth_headers).json()
    assert [r["id"] for r in listed] == [review.id]


def test_generate_reply_uses_correct_rating_band(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    negative = make_review(db_session, seeded_user["store"], platforms, rating=1, content="별로예요")
    res = client.post(f"/reviews/{negative.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert "죄송합니다" in res.json()["content"]  # template_low 문구


def test_save_final_reply_transitions_status_and_blocks_duplicate(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    review = make_review(db_session, seeded_user["store"], platforms, rating=5)

    save = client.post(f"/reviews/{review.id}/reply", json={"style_id": reply_styles.id, "content": "감사합니다!"}, headers=auth_headers)
    assert save.status_code == 200

    answered = client.get("/reviews?status=answered", headers=auth_headers).json()
    assert answered[0]["final_reply"]["content"] == "감사합니다!"

    dup = client.post(f"/reviews/{review.id}/reply", json={"style_id": reply_styles.id, "content": "중복"}, headers=auth_headers)
    assert dup.status_code == 409


def test_reviews_scoped_to_own_store_not_other_users(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    from app.auth import hash_password
    from app.models import Store, User

    other = User(email="other@test.com", password_hash=hash_password("x"), nickname="다른사장", created_at=review_created_at())
    db_session.add(other)
    db_session.flush()
    other_store = Store(user_id=other.id, name="다른가게", category="분식", created_at=review_created_at())
    db_session.add(other_store)
    db_session.commit()

    other_review = make_review(db_session, other_store, platforms, rating=5)

    res = client.post(f"/reviews/{other_review.id}/reply", json={"style_id": reply_styles.id, "content": "몰래"}, headers=auth_headers)
    assert res.status_code == 404  # 다른 사장의 리뷰에는 접근 불가


def review_created_at():
    return datetime.now(timezone.utc)
