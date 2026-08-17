from datetime import date, datetime, timezone

from app.models import Review, Subscription


def make_review(db_session, store, platforms, rating, content="테스트 리뷰"):
    review = Review(
        store_id=store.id, platform_id=platforms["baemin"].id, menu_summary="양념치킨",
        rating=rating, content=content, customer_nickname="먹보",
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


def test_review_without_order_is_listed_and_repliable(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    """order_id 없이(배민 스크래핑처럼) 만든 리뷰도 정상 조회/답글 생성이 되는지 확인."""
    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="양념치킨",
        rating=5, content="주문 연결 없는 리뷰", customer_nickname="먹보",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    listed = client.get("/reviews", headers=auth_headers).json()
    matched = next(r for r in listed if r["id"] == review.id)
    assert matched["order_id"] is None
    assert matched["menu_summary"] == "양념치킨"

    res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert res.status_code == 200
    assert "양념치킨" in res.json()["content"]


def test_reviews_filtered_by_platform_shop_no(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    """한 연결(connection)에 여러 배민 브랜드가 딸린 경우, platform_shop_no
    쿼리 파라미터로 특정 브랜드의 리뷰만 걸러낼 수 있어야 한다."""
    review_a = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴A",
        platform_shop_no="11111", rating=5, content="브랜드A 리뷰", customer_nickname="고객A",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    )
    review_b = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴B",
        platform_shop_no="22222", rating=4, content="브랜드B 리뷰", customer_nickname="고객B",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([review_a, review_b])
    db_session.commit()

    listed = client.get("/reviews?platform_shop_no=11111", headers=auth_headers).json()
    assert [r["id"] for r in listed] == [review_a.id]
    assert listed[0]["platform_shop_no"] == "11111"


def test_reviews_filtered_by_date_range(client, db_session, seeded_user, platforms, auth_headers):
    old_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="1월 리뷰", customer_nickname="고객1",
        customer_order_count=1, created_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    )
    in_range_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="2월 리뷰", customer_nickname="고객2",
        customer_order_count=1, created_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
    )
    boundary_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="2월 마지막날 늦은 시각 리뷰", customer_nickname="고객3",
        customer_order_count=1, created_at=datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc),
    )
    later_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="3월 리뷰", customer_nickname="고객4",
        customer_order_count=1, created_at=datetime(2026, 3, 1, 0, 30, tzinfo=timezone.utc),
    )
    db_session.add_all([old_review, in_range_review, boundary_review, later_review])
    db_session.commit()

    listed = client.get("/reviews?date_from=2026-02-01&date_to=2026-02-28", headers=auth_headers).json()
    ids = {r["id"] for r in listed}
    assert ids == {in_range_review.id, boundary_review.id}  # date_to는 그날 끝까지 포함


def test_reviews_date_from_only_includes_everything_after(client, db_session, seeded_user, platforms, auth_headers):
    old_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="1월 리뷰", customer_nickname="고객1",
        customer_order_count=1, created_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    )
    recent_review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, menu_summary="메뉴",
        rating=5, content="3월 리뷰", customer_nickname="고객2",
        customer_order_count=1, created_at=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([old_review, recent_review])
    db_session.commit()

    listed = client.get("/reviews?date_from=2026-02-01", headers=auth_headers).json()
    assert [r["id"] for r in listed] == [recent_review.id]


def test_reviews_invalid_date_format_returns_400(client, seeded_user, auth_headers):
    res = client.get("/reviews?date_from=2026/02/01", headers=auth_headers)
    assert res.status_code == 400


def test_generate_reply_blocks_after_daily_limit_for_basic_plan(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    reviews = [make_review(db_session, seeded_user["store"], platforms, rating=5) for _ in range(11)]

    for review in reviews[:10]:
        res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
        assert res.status_code == 200

    res = client.post(f"/reviews/{reviews[10].id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "reply_limit_exceeded"


def test_generate_reply_unlimited_for_pro_plan(client, db_session, seeded_user, platforms, reply_styles, auth_headers):
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()

    reviews = [make_review(db_session, seeded_user["store"], platforms, rating=5) for _ in range(11)]
    for review in reviews:
        res = client.post(f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers)
        assert res.status_code == 200
