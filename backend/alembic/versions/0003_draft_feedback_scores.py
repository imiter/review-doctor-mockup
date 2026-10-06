"""draft feedback scores

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07

LangSmith 연동 + 측정 대시보드 플랜(docs/superpowers/plans/
2026-10-07-langsmith-measurement-dashboard.md)의 스키마 변경. 내용:
  - review_replies.trace_id, onboarding_scenarios.trace_id — 이 초안을
    만든 run_agent 호출의 LangSmith run id(nullable, 기존 행은 전부
    NULL로 남는다 — 소급 측정 대상 아님).
  - draft_feedback_scores 신규 테이블 — AI 초안 vs 사장님 최종본 코사인
    유사도 1건당 1행(스펙 4.2절 "순환 측정 장치").
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE review_replies ADD COLUMN trace_id VARCHAR(36)")
    op.execute("ALTER TABLE onboarding_scenarios ADD COLUMN trace_id VARCHAR(36)")

    op.execute("""
        CREATE TABLE draft_feedback_scores (
            id                 BIGSERIAL PRIMARY KEY,
            store_id           BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            category           VARCHAR(24)  NOT NULL,
            similarity_score   NUMERIC(5,4) NOT NULL CHECK (similarity_score BETWEEN -1 AND 1),
            trace_id           VARCHAR(36)  NOT NULL,
            source_review_id   BIGINT       REFERENCES reviews(id) ON DELETE SET NULL,
            source_scenario_id BIGINT       REFERENCES onboarding_scenarios(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_draft_feedback_scores_lookup ON draft_feedback_scores(store_id, category, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE draft_feedback_scores")
    op.execute("ALTER TABLE onboarding_scenarios DROP COLUMN trace_id")
    op.execute("ALTER TABLE review_replies DROP COLUMN trace_id")
