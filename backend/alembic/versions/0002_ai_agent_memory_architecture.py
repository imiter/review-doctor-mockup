"""ai agent memory architecture

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06

AI 에이전트 메모리 아키텍처 1차 작업(docs/superpowers/plans/
2026-10-06-ai-agent-memory-architecture.md, 커밋 ae2b960..d7b3120)이 만든
DDL 변경 전부 + 그 최종 리뷰에서 나온 `golden_examples.reply_embedding`
추가를 한 마이그레이션으로 묶었다. 커밋 범위가 한 계획의 8개 태스크라
스키마 관점에서는 하나의 묶음으로 배포되는 게 맞다.

내용:
  - 절차 기억: `procedural_rules` 신규 테이블(generate.py에 하드코딩돼
    있던 지시문들을 DB로 옮긴 것).
  - 의미 기억: `brand_ceo_notices` 신규 테이블(배민 사장님공지) + 조회 인덱스.
  - 일화 기억(`golden_examples`): `needs_confirmation` 컬럼 추가,
    source CHECK 교체('synthetic' 제거 / 'organic_direct' 추가),
    조회 인덱스를 is_manual/is_synthetic 기준에서 source 기준으로 교체.
  - 말투 일관성 체크 정정: `reply_embedding vector(1024)` 컬럼 추가
    (최종 리뷰 C3 — 기존 체크가 리뷰 내용 임베딩을 비교하고 있었다).

`procedural_rules` 시드 데이터는 이 마이그레이션에 넣지 않는다 —
`backend/scripts/seed_procedural_rules.py`가 멱등하게 담당한다(스키마와
데이터를 섞지 않는다는 이 프로젝트의 기존 구분: schema.sql / seed.sql).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 절차 기억 ──────────────────────────────────────────────────────────
    # 전역 테이블이다(store_id 없음) — "이 AI 제품이 어떻게 답글을 쓰는가"에
    # 대한 제품 차원 기준이라 모든 구독 매장에 동일하게 적용된다(스펙 1.1절).
    op.execute("""
        CREATE TABLE procedural_rules (
            id               BIGSERIAL PRIMARY KEY,
            rule_key         VARCHAR(40)  NOT NULL UNIQUE,
            instruction_text TEXT         NOT NULL,
            active           BOOLEAN      NOT NULL DEFAULT true,
            description      TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)

    # ── 의미 기억: 사장님공지 ─────────────────────────────────────────────
    # 히스토리를 안 남기고 매 동기화마다 (connection_id, shop_no) 단위로
    # 전체 교체하는 테이블이라 UNIQUE 제약을 걸지 않는다(동기화 로직이
    # 먼저 지우고 다시 넣으므로 중복이 구조적으로 생기지 않는다).
    op.execute("""
        CREATE TABLE brand_ceo_notices (
            id                 BIGSERIAL PRIMARY KEY,
            connection_id      BIGINT       NOT NULL REFERENCES store_platform_connections(id) ON DELETE CASCADE,
            shop_no            VARCHAR(20)  NOT NULL,
            external_notice_id BIGINT       NOT NULL,
            contents           TEXT         NOT NULL,
            display_status     VARCHAR(16)  NOT NULL,
            block_type         VARCHAR(16)  NOT NULL,
            notice_created_at  TIMESTAMPTZ  NOT NULL,
            synced_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_brand_ceo_notices_lookup ON brand_ceo_notices(connection_id, shop_no)")

    # ── 일화 기억: golden_examples ────────────────────────────────────────
    # needs_confirmation: 경로 C(배민 직접 답글) 승격 시 말투 이상치로
    # 판정된 행을 표시한다. 기존 행은 전부 false(= 확인 필요 없음)로
    # 시작하는 게 맞다 — 이미 경로 A/B로 들어온 신뢰 가능한 데이터다.
    op.execute("ALTER TABLE golden_examples ADD COLUMN needs_confirmation BOOLEAN NOT NULL DEFAULT false")

    # reply_embedding: reply_text 임베딩(말투 일관성 체크 기준).
    # 기존 행은 NULL로 남고, backend/scripts/backfill_golden_example_reply_embeddings.py가
    # 나중에 채운다 — embedding 컬럼을 추가했을 때와 같은 방식이다.
    op.execute("ALTER TABLE golden_examples ADD COLUMN reply_embedding vector(1024)")

    # source CHECK 교체 — 'synthetic'(순수 AI 생성 모범답안으로 예시를
    # 증강하는 메커니즘)을 제거하고 'organic_direct'(경로 C)를 넣는다.
    # 제약 이름은 Postgres가 자동 생성하는 `<table>_<column>_check` 규칙을
    # 따르므로(원본 schema.sql이 인라인 CHECK로 만들었다) 그 이름으로
    # 지운다. 'synthetic' 행이 운영 DB에 실제로 있으면 새 CHECK 추가가
    # 실패하는데, 그건 조용히 통과하는 것보다 나은 결과다 — 이 계획이
    # synthetic 메커니즘 자체를 제거했으므로 그런 행은 사람이 보고
    # 결정해야 한다(실측: 이 메커니즘은 끝까지 "채택하지 않음"으로
    # 남아 생성 경로가 없었다).
    op.execute("ALTER TABLE golden_examples DROP CONSTRAINT IF EXISTS golden_examples_source_check")
    op.execute("""
        ALTER TABLE golden_examples ADD CONSTRAINT golden_examples_source_check
            CHECK (source IN ('backfill', 'organic', 'organic_direct', 'onboarding'))
    """)

    # 조회(fetch_golden_examples)가 실제로 거르는 컬럼이 is_manual/is_synthetic에서
    # source로 바뀌었으니 인덱스도 같이 교체한다.
    op.execute("DROP INDEX IF EXISTS idx_golden_examples_lookup")
    op.execute("""
        CREATE INDEX idx_golden_examples_lookup
            ON golden_examples(store_id, category, source, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_golden_examples_lookup")
    op.execute("""
        CREATE INDEX idx_golden_examples_lookup
            ON golden_examples(store_id, category, is_manual, is_synthetic, created_at DESC)
    """)

    op.execute("ALTER TABLE golden_examples DROP CONSTRAINT IF EXISTS golden_examples_source_check")
    # 되돌릴 때는 'organic_direct' 행이 이미 들어와 있을 수 있으므로, 그
    # 행들을 먼저 'organic'으로 내린 뒤 옛 CHECK를 복원한다 — 경로 C는
    # "사장님이 직접 쓴 진짜 답글"이라는 점에서 organic과 같은 성격이라
    # 이 강등이 데이터 의미를 크게 훼손하지 않는다.
    op.execute("UPDATE golden_examples SET source = 'organic' WHERE source = 'organic_direct'")
    op.execute("""
        ALTER TABLE golden_examples ADD CONSTRAINT golden_examples_source_check
            CHECK (source IN ('backfill', 'organic', 'onboarding', 'synthetic'))
    """)

    op.execute("ALTER TABLE golden_examples DROP COLUMN reply_embedding")
    op.execute("ALTER TABLE golden_examples DROP COLUMN needs_confirmation")

    op.execute("DROP TABLE brand_ceo_notices")
    op.execute("DROP TABLE procedural_rules")
