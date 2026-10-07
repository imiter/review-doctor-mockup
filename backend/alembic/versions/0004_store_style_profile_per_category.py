"""store style profile per category

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

DeepTwin 원칙 UI 플랜(docs/superpowers/plans/
2026-10-07-deeptwin-principle-ui.md)의 스키마 변경. store_style_profile을
매장당 1행에서 (store_id, category)당 1행으로 재설계한다 — 스펙 4.1절
"원칙 레이어 고도화"(카테고리별 판단 원칙 증류).

기존 행은 전부 버린다 — store_style_profile은 golden_examples에서 언제든
다시 뽑아낼 수 있는 캐시이고, 바뀐 스키마에서는 기존 "매장 전체 통합" 행이
어떤 카테고리에도 정확히 대응하지 않는다. 다음 저장(save_final_reply/
answer_scenario) 시점에 해당 카테고리 행이 자연히 다시 생긴다 — 데이터
유실이 아니라 콜드스타트다. 이 테이블의 이전 마이그레이션(0001)도 아직
운영 DB에 적용 전이라 실제로 버려지는 운영 데이터는 없다.
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE store_style_profile")
    op.execute("""
        CREATE TABLE store_style_profile (
            store_id             BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            category             VARCHAR(24)  NOT NULL,
            rules                TEXT         NOT NULL,
            generated_from_count INT          NOT NULL,
            needs_confirmation   BOOLEAN      NOT NULL DEFAULT false,
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (store_id, category)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE store_style_profile")
    op.execute("""
        CREATE TABLE store_style_profile (
            store_id             BIGINT       PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
            rules                TEXT         NOT NULL,
            generated_from_count INT          NOT NULL,
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
