from datetime import datetime

from app.models import ReplyStyle, ReplyTemplate, Review
from tests.test_models_reviews import make_sp


def setup_review(db_session, rating=5):
    sp = make_sp(db_session)
    style = ReplyStyle(name="친근함", description="따뜻한 말투")
    db_session.add(style)
    db_session.flush()
    db_session.add(ReplyTemplate(
        style_id=style.id, rating_band="high",
        template_text="{reviewer_name}님 감사해요!",
    ))
    review = Review(
        store_platform_id=sp.id, rating=rating, content="맛있어요",
        reviewer_name="먹보", has_photo=False, created_at=datetime(2026, 7, 20, 18, 0),
    )
    db_session.add(review)
    db_session.commit()
    return review, style


def test_list_reviews(client, db_session):
    review, _ = setup_review(db_session)
    res = client.get("/api/reviews")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["platform_name"] == "배달의민족"
    assert body[0]["reply"] is None


def test_draft_fills_template(client, db_session):
    review, style = setup_review(db_session)
    res = client.post(f"/api/reviews/{review.id}/reply/draft", json={"style_id": style.id})
    assert res.status_code == 200
    assert res.json()["content"] == "먹보님 감사해요!"


def test_save_reply_transitions_status(client, db_session):
    review, style = setup_review(db_session)
    res = client.post(
        f"/api/reviews/{review.id}/reply",
        json={"style_id": style.id, "content": "먹보님 감사해요! 또 오세요."},
    )
    assert res.status_code == 200
    listed = client.get("/api/reviews?status=answered").json()
    assert len(listed) == 1
    assert listed[0]["reply"]["content"] == "먹보님 감사해요! 또 오세요."

    dup = client.post(
        f"/api/reviews/{review.id}/reply",
        json={"style_id": style.id, "content": "중복"},
    )
    assert dup.status_code == 409
