from datetime import datetime, timezone

from app.models import DraftFeedbackScore, User


def _promote_to_admin(db_session, user: User) -> None:
    user.role = "admin"
    db_session.commit()


def test_list_runs_requires_admin_role(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/runs", headers=auth_headers)
    assert res.status_code == 403


def test_list_runs_returns_shaped_rows(client, db_session, seeded_user, auth_headers, monkeypatch):
    _promote_to_admin(db_session, seeded_user["user"])
    import app.routers.admin_llmops as admin_llmops_mod

    monkeypatch.setattr(
        admin_llmops_mod.observability, "list_recent_runs",
        lambda limit: [{"trace_id": "t1", "category_label": "배달(지연/파손)", "passed_verification": True}],
    )

    res = client.get("/admin/llmops/runs?limit=5", headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == {"runs": [{"trace_id": "t1", "category_label": "배달(지연/파손)", "passed_verification": True}]}


def test_run_detail_requires_admin_role(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/runs/some-trace-id", headers=auth_headers)
    assert res.status_code == 403


def test_run_detail_returns_404_when_not_found(client, db_session, seeded_user, auth_headers, monkeypatch):
    _promote_to_admin(db_session, seeded_user["user"])
    import app.routers.admin_llmops as admin_llmops_mod

    monkeypatch.setattr(admin_llmops_mod.observability, "get_run_detail", lambda trace_id: None)

    res = client.get("/admin/llmops/runs/nonexistent", headers=auth_headers)

    assert res.status_code == 404


def test_run_detail_returns_node_data(client, db_session, seeded_user, auth_headers, monkeypatch):
    _promote_to_admin(db_session, seeded_user["user"])
    import app.routers.admin_llmops as admin_llmops_mod

    detail = {"trace_id": "t1", "nodes": [{"name": "retrieve_memory", "outputs": {"style_rules": "규칙"}}]}
    monkeypatch.setattr(admin_llmops_mod.observability, "get_run_detail", lambda trace_id: detail)

    res = client.get("/admin/llmops/runs/t1", headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == detail


def test_accuracy_requires_admin_role(client, seeded_user, auth_headers):
    res = client.get("/admin/llmops/accuracy", headers=auth_headers)
    assert res.status_code == 403


def test_accuracy_aggregates_across_all_stores(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])
    sid = seeded_user["store"].id
    db_session.add_all([
        DraftFeedbackScore(
            store_id=sid, category="delivery", similarity_score=0.9, trace_id="t1",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=sid, category="delivery", similarity_score=0.7, trace_id="t2",
            created_at=datetime.now(timezone.utc),
        ),
        DraftFeedbackScore(
            store_id=sid, category="no_issue", similarity_score=0.95, trace_id="t3",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    res = client.get("/admin/llmops/accuracy", headers=auth_headers)

    assert res.status_code == 200
    by_category = {c["category"]: c for c in res.json()["categories"]}
    assert by_category["delivery"]["sample_count"] == 2
    assert by_category["delivery"]["avg_similarity"] == 0.8
    assert by_category["delivery"]["label"] == "배달(지연/파손)"
    assert by_category["no_issue"]["label"] == "특이 불만 없음"


def test_accuracy_empty_when_no_scores(client, db_session, seeded_user, auth_headers):
    _promote_to_admin(db_session, seeded_user["user"])

    res = client.get("/admin/llmops/accuracy", headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == {"categories": []}
