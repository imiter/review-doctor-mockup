from app.models import ProceduralRule
from scripts.seed_procedural_rules import run


def test_seed_procedural_rules_creates_all_five(db_session):
    run(db_session)
    db_session.commit()

    rules = {r.rule_key: r for r in db_session.query(ProceduralRule).all()}
    assert set(rules) == {
        "complaint_tone_override", "few_shot_anti_overfit", "menu_grounding",
        "no_issue_framing", "delivery_boundary",
    }
    assert all(r.active for r in rules.values())


def test_seed_procedural_rules_is_idempotent(db_session):
    run(db_session)
    run(db_session)  # 두 번 실행해도 중복 안 생김
    db_session.commit()

    count = db_session.query(ProceduralRule).count()
    assert count == 5
