"""procedural_rules 초기 데이터 — 기존에 backend/app/llm/generate.py에
하드코딩돼 있던 지시문 4개 + 신규 delivery 경계 규칙 1개를 DB로 옮긴다
(스펙 2026-10-03-ai-agent-llmops-reply-design.md 1.1절). rule_key가 이미
있으면 건드리지 않는다(멱등) — 운영 중 사람이 instruction_text를 고쳐둔
걸 재실행이 덮어쓰면 안 된다."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProceduralRule

_RULES: dict[str, tuple[str, str]] = {
    "complaint_tone_override": (
        "불만이 담긴 리뷰입니다(위생/안전 문제이거나 별점과 내용이 어긋나는 "
        "경우 포함). 페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 "
        "작성하세요.",
        "불만 리뷰(category != no_issue, is_sensitive, sentiment_conflict)에 적용되는 톤 강제 규칙",
    ),
    "few_shot_anti_overfit": (
        "이 예시들은 말투·태도·구조(원인 설명 → 사과 → 재방문 유도)만 "
        "참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은 반드시 "
        "\"이번 리뷰의 실제 상황\"에만 근거해 새로 작성하라.",
        "few-shot 예시 과적합(문장 그대로 복사) 방지 지시",
    ),
    "menu_grounding": (
        "리뷰가 특정 메뉴나 재료를 언급하면 반드시 이 정보를 근거로 삼아라 "
        "— 실제 메뉴 구성과 다른 원인(예: \"신메뉴라서\", \"양을 줄였다\")을 "
        "추측해서 쓰지 마라. 여기 없는 내용(오늘 그 배치의 조리 상태 등)은 "
        "사장님만 아는 사실이니 지어내지 말고 일반적인 사과로 넘어가라.",
        "brand_menu_info 그라운딩 데이터 사용 지시",
    ),
    "no_issue_framing": (
        "특이 불만 없음(칭찬 또는 중립적인 리뷰). 리뷰에 구체적인 취향/요청이 "
        "담겨있으면 자연스럽게 반영하고, 없으면 감사 인사 위주로 답하세요.",
        "category == no_issue 리뷰의 프레이밍",
    ),
    "delivery_boundary": (
        "배달 지연·파손·라이더(배달원) 응대 등 가게가 직접 통제할 수 없는 "
        "배달 과정 불만에는 공감과 유감을 표현하되, 가게가 전적으로 "
        "책임지거나 무조건 '수정하겠습니다'라고 약속하지 않는다. 가게 "
        "책임이 아님을 부드럽게 알리면서도 고객 불편엔 공감하는 균형을 "
        "유지하세요.",
        "category == delivery 리뷰 전용 — 가게 통제 밖 불만의 책임 경계 (신규, 2026-10-06)",
    ),
}


def run(db: Session) -> None:
    # Flush any pending adds so the next query sees them (session has autoflush=False in tests)
    db.flush()
    existing = set(db.scalars(select(ProceduralRule.rule_key)).all())
    for key, (text, description) in _RULES.items():
        if key in existing:
            continue
        db.add(ProceduralRule(
            rule_key=key, instruction_text=text, active=True,
            description=description, created_at=datetime.now(timezone.utc),
        ))


if __name__ == "__main__":
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        run(session)
        session.commit()
    finally:
        session.close()
