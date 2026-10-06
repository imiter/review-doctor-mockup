"""baseline schema before ai agent memory architecture

Revision ID: 0001
Revises:
Create Date: 2026-10-06

이 프로젝트는 2026-10-06까지 Alembic 없이 `schema.sql`을 손으로 적용해 왔다
(CLAUDE.md "Alembic 도입" 절 참고). 이 마이그레이션은 그 전환점의 **기준선**
이다 — 커밋 ae2b960(AI 에이전트 메모리 아키텍처 작업 직전) 시점의
`schema.sql` DDL을 그대로 옮긴 것이고, 이후 변경은 전부 0002부터 별도
마이그레이션으로 쌓인다.

원본 `schema.sql`과 다른 점은 딱 두 가지다:
  1. `BEGIN;`/`COMMIT;`를 뺐다 — Alembic이 이미 마이그레이션 하나를 한
     트랜잭션으로 감싼다.
  2. 맨 앞의 `DROP TABLE IF EXISTS ... CASCADE`를 뺐다 — 이건 로컬에서
     스키마를 통째로 재적용하기 위한 개발 편의 구문이고, 운영 DB에 돌리면
     데이터를 전부 날린다. 마이그레이션에는 절대로 들어가면 안 된다.

이미 이 스키마가 적용된 DB(= 운영 DB)에는 이 마이그레이션을 **실행하지 않고**
`alembic stamp 0001`로 "이미 기준선까지 와 있다"고 표시만 한다. 빈 DB에서는
`alembic upgrade head`가 0001 → 0002 순서로 전체 스키마를 만든다.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# FK 의존 순서대로 — 원본 schema.sql의 테이블 순서를 그대로 유지한다.
_STATEMENTS = [
    # golden_examples.embedding(벡터 검색, 2026-08-26)이 쓰는 pgvector 확장.
    "CREATE EXTENSION IF NOT EXISTS vector",

    # 1. users — 사장 계정 (컬럼 최소화, 개인정보 비식별화)
    """
    CREATE TABLE users (
        id               BIGSERIAL PRIMARY KEY,
        email            VARCHAR(255) UNIQUE,
        password_hash    VARCHAR(255),
        nickname         VARCHAR(50)  NOT NULL,
        phone_hash       CHAR(64),
        marketing_agreed BOOLEAN      NOT NULL DEFAULT FALSE,
        role             VARCHAR(10)  NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'admin')),
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,

    # 2. stores — 매장. users 1:N stores
    """
    CREATE TABLE stores (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name       VARCHAR(100) NOT NULL,
        category   VARCHAR(30)  NOT NULL,
        address    VARCHAR(200),
        created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_stores_user ON stores(user_id)",

    # 3. platforms — 배달 플랫폼 마스터
    """
    CREATE TABLE platforms (
        id                      SERIAL PRIMARY KEY,
        code                    VARCHAR(20) NOT NULL UNIQUE,
        name                    VARCHAR(50) NOT NULL,
        brand_color             VARCHAR(7),
        default_commission_rate NUMERIC(5,4)
    )
    """,

    # 4. store_platform_connections — 매장:플랫폼 N:M 중간 테이블
    """
    CREATE TABLE store_platform_connections (
        id                    BIGSERIAL PRIMARY KEY,
        store_id              BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id           INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        platform_store_id     VARCHAR(30) NOT NULL,
        business_number       VARCHAR(20),
        credential_ciphertext TEXT,
        connected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (store_id, platform_id)
    )
    """,

    # 5. subscriptions — Basic/Pro 플랜
    """
    CREATE TABLE subscriptions (
        id                BIGSERIAL PRIMARY KEY,
        user_id           BIGINT      NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        plan              VARCHAR(10) NOT NULL DEFAULT 'basic' CHECK (plan IN ('basic', 'pro')),
        daily_reply_limit INT         NOT NULL DEFAULT 10,
        started_at        DATE        NOT NULL,
        expires_at        DATE
    )
    """,

    # 6. reply_styles — 답글 말투 스타일 마스터
    """
    CREATE TABLE reply_styles (
        id                SERIAL PRIMARY KEY,
        name              VARCHAR(30)  NOT NULL UNIQUE,
        description       VARCHAR(200) NOT NULL,
        template_high     TEXT         NOT NULL,
        template_mid      TEXT         NOT NULL,
        template_low      TEXT         NOT NULL,
        tone_instruction  TEXT         NOT NULL DEFAULT ''
    )
    """,

    # 7. reply_settings — 가게별 답글 설정. stores 1:1
    """
    CREATE TABLE reply_settings (
        id                    BIGSERIAL PRIMARY KEY,
        store_id              BIGINT       NOT NULL UNIQUE REFERENCES stores(id)       ON DELETE CASCADE,
        style_id              INT          NOT NULL        REFERENCES reply_styles(id) ON DELETE RESTRICT,
        promo_text            VARCHAR(400),
        include_nickname      BOOLEAN      NOT NULL DEFAULT TRUE,
        include_menu          BOOLEAN      NOT NULL DEFAULT TRUE,
        include_store_name    BOOLEAN      NOT NULL DEFAULT TRUE,
        promo_on_negative     BOOLEAN      NOT NULL DEFAULT FALSE,
        auto_reply_enabled    BOOLEAN      NOT NULL DEFAULT FALSE,
        auto_reply_min_rating SMALLINT     NOT NULL DEFAULT 1 CHECK (auto_reply_min_rating BETWEEN 1 AND 5)
    )
    """,

    # 8. orders — 주문내역. stores 1:N orders
    """
    CREATE TABLE orders (
        id           BIGSERIAL PRIMARY KEY,
        store_id     BIGINT       NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id  INT          NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        order_no     VARCHAR(30)  NOT NULL UNIQUE,
        ordered_at   TIMESTAMPTZ  NOT NULL,
        menu_summary VARCHAR(200) NOT NULL,
        order_type   VARCHAR(10)  NOT NULL CHECK (order_type IN ('delivery', 'takeout')),
        amount       INT          NOT NULL CHECK (amount >= 0)
    )
    """,
    "CREATE INDEX idx_orders_store_time    ON orders(store_id, ordered_at)",
    "CREATE INDEX idx_orders_platform_time ON orders(platform_id, ordered_at)",

    # 9. reviews — 리뷰
    """
    CREATE TABLE reviews (
        id                   BIGSERIAL PRIMARY KEY,
        order_id             BIGINT      UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
        store_id             BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id          INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        menu_summary         VARCHAR(200) NOT NULL,
        external_review_id   BIGINT      UNIQUE,
        platform_shop_no     VARCHAR(20),
        rating               SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
        content              TEXT        NOT NULL,
        customer_nickname    VARCHAR(50) NOT NULL,
        customer_order_count INT         NOT NULL DEFAULT 1,
        status               VARCHAR(12) NOT NULL DEFAULT 'unanswered'
                             CHECK (status IN ('unanswered', 'pending', 'answered')),
        category             VARCHAR(24) NOT NULL DEFAULT 'no_issue'
                             CHECK (category IN (
                                 'food_quality', 'delivery', 'hygiene', 'service',
                                 'price', 'missing_or_wrong_item', 'no_issue'
                             )),
        is_sensitive         BOOLEAN     NOT NULL DEFAULT FALSE,
        sentiment_conflict   BOOLEAN     NOT NULL DEFAULT FALSE,
        image_urls           TEXT[]      NOT NULL DEFAULT '{}',
        created_at           TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX idx_reviews_status ON reviews(status)",
    "CREATE INDEX idx_reviews_store  ON reviews(store_id)",
    "CREATE INDEX idx_reviews_platform_shop ON reviews(platform_shop_no)",
    "CREATE INDEX idx_reviews_category ON reviews(store_id, category)",

    # 10. review_replies — 답글. reviews 1:N
    """
    CREATE TABLE review_replies (
        id         BIGSERIAL PRIMARY KEY,
        review_id  BIGINT      NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
        reply_type VARCHAR(10) NOT NULL CHECK (reply_type IN ('ai_draft', 'final', 'secondary')),
        style_id   INT         REFERENCES reply_styles(id) ON DELETE SET NULL,
        content    TEXT        NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_review_replies_review ON review_replies(review_id)",

    # 11. daily_settlements — 일별 매출/입금 + 배민 정산 상세 실측 4컬럼(nullable)
    """
    CREATE TABLE daily_settlements (
        id                        BIGSERIAL PRIMARY KEY,
        store_id                  BIGINT NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id               INT    NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        settle_date               DATE   NOT NULL,
        sales_amount              INT    NOT NULL DEFAULT 0 CHECK (sales_amount >= 0),
        deposit_amount            INT    NOT NULL DEFAULT 0 CHECK (deposit_amount >= 0),
        commission_amount         INT    CHECK (commission_amount >= 0),
        delivery_fee_amount       INT    CHECK (delivery_fee_amount >= 0),
        customer_discount_amount  INT    CHECK (customer_discount_amount >= 0),
        ad_cost_amount            INT    CHECK (ad_cost_amount >= 0),
        UNIQUE (store_id, platform_id, settle_date)
    )
    """,

    # 12. repurchase_metrics — 날짜별 재주문율
    """
    CREATE TABLE repurchase_metrics (
        id            BIGSERIAL PRIMARY KEY,
        store_id      BIGINT       NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id   INT          NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        metric_date   DATE         NOT NULL,
        new_orders    INT          NOT NULL DEFAULT 0,
        repeat_orders INT          NOT NULL DEFAULT 0,
        rate_raw      NUMERIC(5,4) NOT NULL CHECK (rate_raw      BETWEEN 0 AND 1),
        rate_adjusted NUMERIC(5,4) NOT NULL CHECK (rate_adjusted BETWEEN 0 AND 1),
        UNIQUE (store_id, platform_id, metric_date)
    )
    """,

    # 13. ad_campaigns — 광고 캠페인. stores 1:N
    """
    CREATE TABLE ad_campaigns (
        id          BIGSERIAL PRIMARY KEY,
        store_id    BIGINT      NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
        category    VARCHAR(30) NOT NULL,
        current_cpc INT         NOT NULL CHECK (current_cpc >= 0),
        target_rank SMALLINT    NOT NULL CHECK (target_rank >= 1),
        status      VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
        shop_no     VARCHAR(20)
    )
    """,
    "CREATE INDEX idx_ad_campaigns_store ON ad_campaigns(store_id)",

    # 14. ad_performance_metrics — 일별 광고 성과 원본
    """
    CREATE TABLE ad_performance_metrics (
        id          BIGSERIAL PRIMARY KEY,
        campaign_id BIGINT NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
        metric_date DATE   NOT NULL,
        ad_spend    INT    NOT NULL DEFAULT 0 CHECK (ad_spend   >= 0),
        clicks      INT    NOT NULL DEFAULT 0 CHECK (clicks     >= 0),
        ad_orders   INT    NOT NULL DEFAULT 0 CHECK (ad_orders  >= 0),
        ad_revenue  INT    NOT NULL DEFAULT 0 CHECK (ad_revenue >= 0),
        UNIQUE (campaign_id, metric_date)
    )
    """,

    # 15. ad_rank_snapshots — 순위 스냅샷(시간별 Mock + 반경별 실측 공존)
    """
    CREATE TABLE ad_rank_snapshots (
        id                 BIGSERIAL PRIMARY KEY,
        campaign_id        BIGINT      NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
        snapshot_at        TIMESTAMPTZ NOT NULL,
        current_rank       SMALLINT    NOT NULL CHECK (current_rank >= 1),
        competitor_est_cpc INT         CHECK (competitor_est_cpc >= 0),
        status             VARCHAR(12) CHECK (status IN ('normal', 'rank_dropped')),
        recommended_action VARCHAR(10) NOT NULL DEFAULT 'keep'
                           CHECK (recommended_action IN ('keep', 'raise_cpc', 'lower_cpc')),
        suggested_cpc      INT         CHECK (suggested_cpc >= 0),
        distance_km        NUMERIC(4,2) CHECK (distance_km >= 0),
        point_label        VARCHAR(20),
        total_scanned      SMALLINT    CHECK (total_scanned >= 0),
        ads_above          SMALLINT    CHECK (ads_above >= 0),
        bid_at_snapshot    INT         CHECK (bid_at_snapshot >= 0),
        UNIQUE (campaign_id, snapshot_at)
    )
    """,

    # 16. alerts — 알림
    """
    CREATE TABLE alerts (
        id         BIGSERIAL PRIMARY KEY,
        store_id   BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
        alert_type VARCHAR(20)  NOT NULL
                   CHECK (alert_type IN ('negative_review', 'unanswered_review', 'rank_drop', 'sensitive_review')),
        message    VARCHAR(300) NOT NULL,
        is_read    BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_alerts_store_unread ON alerts(store_id, is_read)",

    # 16-1. golden_examples — RAG few-shot 소스(이 시점에는 is_manual/is_synthetic 기준)
    """
    CREATE TABLE golden_examples (
        id               BIGSERIAL PRIMARY KEY,
        store_id         BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
        category         VARCHAR(24)  NOT NULL,
        review_text      TEXT         NOT NULL,
        reply_text       TEXT         NOT NULL,
        is_manual        BOOLEAN      NOT NULL,
        is_synthetic     BOOLEAN      NOT NULL,
        source           VARCHAR(16)  NOT NULL
                         CHECK (source IN ('backfill', 'organic', 'onboarding', 'synthetic')),
        source_review_id BIGINT       REFERENCES reviews(id) ON DELETE SET NULL,
        source_reply_id  BIGINT       REFERENCES review_replies(id) ON DELETE SET NULL,
        embedding        vector(1024),
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX idx_golden_examples_lookup
        ON golden_examples(store_id, category, is_manual, is_synthetic, created_at DESC)
    """,

    # 16-2. store_style_profile — 매장별 답글 스타일 규칙 캐싱
    """
    CREATE TABLE store_style_profile (
        store_id             BIGINT       PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
        rules                TEXT         NOT NULL,
        generated_from_count INT          NOT NULL,
        updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,

    # 17. social_accounts — 소셜 로그인 연결
    """
    CREATE TABLE social_accounts (
        id                BIGSERIAL PRIMARY KEY,
        user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider          VARCHAR(20) NOT NULL,
        provider_user_id  VARCHAR(100) NOT NULL,
        connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (provider, provider_user_id)
    )
    """,
    "CREATE INDEX idx_social_accounts_user ON social_accounts(user_id)",

    # 18. signup_verifications — 이메일 인증 코드
    """
    CREATE TABLE signup_verifications (
        id         BIGSERIAL PRIMARY KEY,
        target     VARCHAR(255) NOT NULL,
        purpose    VARCHAR(20)  NOT NULL CHECK (purpose IN ('email', 'phone', 'password_reset')),
        code_hash  CHAR(64)     NOT NULL,
        expires_at TIMESTAMPTZ  NOT NULL,
        attempts   INT          NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (target, purpose)
    )
    """,

    # 19. review_sync_jobs — 배민 데이터 동기화 작업 상태
    """
    CREATE TABLE review_sync_jobs (
        id               BIGSERIAL PRIMARY KEY,
        store_id         BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id      INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        status           VARCHAR(10) NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'running', 'success', 'failed')),
        triggered_by     VARCHAR(10) NOT NULL DEFAULT 'manual'
                         CHECK (triggered_by IN ('manual', 'scheduled')),
        reviews_fetched  INT,
        reviews_inserted INT,
        error_message    TEXT,
        started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at      TIMESTAMPTZ
    )
    """,

    # 20. baemin_shop_brands — 배민 계정 하나에 딸린 브랜드 목록
    """
    CREATE TABLE baemin_shop_brands (
        id            BIGSERIAL PRIMARY KEY,
        connection_id BIGINT       NOT NULL REFERENCES store_platform_connections(id) ON DELETE CASCADE,
        shop_no       VARCHAR(20)  NOT NULL,
        shop_name     VARCHAR(200) NOT NULL,
        UNIQUE (connection_id, shop_no)
    )
    """,

    # 21. brand_ad_click_metrics — 브랜드별 일별 우리가게클릭 성과 원본
    """
    CREATE TABLE brand_ad_click_metrics (
        id          BIGSERIAL PRIMARY KEY,
        store_id    BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
        platform_id INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
        shop_no     VARCHAR(20) NOT NULL,
        metric_date DATE        NOT NULL,
        ad_spend    INT NOT NULL DEFAULT 0 CHECK (ad_spend    >= 0),
        impressions INT NOT NULL DEFAULT 0 CHECK (impressions >= 0),
        clicks      INT NOT NULL DEFAULT 0 CHECK (clicks      >= 0),
        ad_orders   INT NOT NULL DEFAULT 0 CHECK (ad_orders   >= 0),
        ad_revenue  INT NOT NULL DEFAULT 0 CHECK (ad_revenue  >= 0),
        UNIQUE (store_id, platform_id, shop_no, metric_date)
    )
    """,

    # 22. payments — 토스페이먼츠 결제(테스트 키)
    """
    CREATE TABLE payments (
        id                     BIGSERIAL PRIMARY KEY,
        user_id                BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        order_id               VARCHAR(64) NOT NULL UNIQUE,
        plan                   VARCHAR(10) NOT NULL DEFAULT 'pro',
        amount                 INT         NOT NULL,
        status                 VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'failed')),
        toss_payment_key       VARCHAR(200),
        fail_reason            VARCHAR(200),
        virtual_account_secret VARCHAR(64),
        requested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        approved_at            TIMESTAMPTZ
    )
    """,
    "CREATE INDEX idx_payments_user ON payments(user_id)",

    # 23. onboarding_scenarios — 답글 온보딩 가상 리뷰/마중물 초안
    """
    CREATE TABLE onboarding_scenarios (
        id                  BIGSERIAL PRIMARY KEY,
        store_id            BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
        category            VARCHAR(24)  NOT NULL,
        virtual_review_text TEXT         NOT NULL,
        draft_text          TEXT         NOT NULL,
        status              VARCHAR(10)  NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'answered', 'skipped')),
        shown_on            DATE,
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (store_id, category)
    )
    """,

    # 24. brand_menu_info — 브랜드별 메뉴관리 화면 텍스트/메뉴 항목
    """
    CREATE TABLE brand_menu_info (
        id            BIGSERIAL PRIMARY KEY,
        connection_id BIGINT       NOT NULL REFERENCES store_platform_connections(id) ON DELETE CASCADE,
        shop_no       VARCHAR(20)  NOT NULL,
        store_intro   TEXT,
        food_origin   TEXT,
        menu_intro    TEXT,
        menu_items    JSONB        NOT NULL DEFAULT '[]',
        updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (connection_id, shop_no)
    )
    """,
]

# 생성의 역순(= FK 역순). 기준선을 되돌리는 건 사실상 "DB를 비우는" 작업이라
# 실제로 쓸 일은 없지만, Alembic이 downgrade() 메서드를 요구하므로 둔다.
_DROP_ORDER = [
    "brand_menu_info", "onboarding_scenarios", "payments", "brand_ad_click_metrics",
    "baemin_shop_brands", "review_sync_jobs", "signup_verifications", "social_accounts",
    "store_style_profile", "golden_examples", "alerts", "ad_rank_snapshots",
    "ad_performance_metrics", "ad_campaigns", "repurchase_metrics", "daily_settlements",
    "review_replies", "reviews", "orders", "reply_settings", "reply_styles",
    "subscriptions", "store_platform_connections", "platforms", "stores", "users",
]


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS " + ", ".join(_DROP_ORDER) + " CASCADE")
