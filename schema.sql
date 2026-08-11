-- ============================================================================
-- Delivery Review & Store Insight MVP — PostgreSQL Schema
-- ============================================================================
-- 21개 테이블. 모든 FK에 ON DELETE 정책 명시.
--
-- 삭제 정책 원칙:
--   ON DELETE CASCADE  — 부모에 종속된 소유 데이터 (사장 탈퇴 → 매장/주문/리뷰 연쇄 삭제)
--   ON DELETE RESTRICT — 마스터 테이블 보호 (참조 중인 플랫폼/스타일은 삭제 불가)
--   ON DELETE SET NULL — 이력 보존 (스타일이 삭제돼도 과거 답글은 남긴다)
--
-- 개인정보 원칙:
--   전화번호는 phone_hash(SHA-256)로만 저장. 원문 저장 금지.
--   사업자번호·스토어 아이디·주문번호는 전부 Mock 값.
--   주민번호·실명은 받지 않는다.
--
-- 금액은 전부 원 단위 정수(INT). 비율(CVR, 재주문율)은 소수 0~1의 NUMERIC.
-- ============================================================================

BEGIN;

DROP TABLE IF EXISTS
    brand_ad_click_metrics, baemin_shop_brands, review_sync_jobs, signup_verifications, social_accounts, alerts, ad_rank_snapshots,
    ad_performance_metrics, ad_campaigns, repurchase_metrics, daily_settlements, review_replies,
    reviews, orders, reply_settings, reply_styles, subscriptions, store_platform_connections,
    platforms, stores, users
CASCADE;

-- ----------------------------------------------------------------------------
-- 1. users — 사장 계정 (컬럼 최소화, 개인정보 비식별화)
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    email            VARCHAR(255) UNIQUE,           -- 카카오 전용 계정은 이메일 없을 수 있음 (비즈니스 미인증)
    password_hash    VARCHAR(255),                  -- bcrypt. 카카오 전용 계정은 NULL (이메일 로그인용, 합의 사항)
    nickname         VARCHAR(50)  NOT NULL,
    phone_hash       CHAR(64),                     -- SHA-256 hex. 전화번호 원문 저장 금지
    marketing_agreed BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2. stores — 매장. users 1:N stores
-- ----------------------------------------------------------------------------
CREATE TABLE stores (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(100) NOT NULL,
    category   VARCHAR(30)  NOT NULL,              -- 치킨, 닭갈비 등 (광고 카테고리와 동일 체계)
    address    VARCHAR(200),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_stores_user ON stores(user_id);

-- ----------------------------------------------------------------------------
-- 3. platforms — 배달 플랫폼 마스터 (로고/수수료율 확장 대비)
-- ----------------------------------------------------------------------------
CREATE TABLE platforms (
    id                      SERIAL PRIMARY KEY,
    code                    VARCHAR(20) NOT NULL UNIQUE,  -- baemin | coupang_eats | yogiyo ...
    name                    VARCHAR(50) NOT NULL,
    brand_color             VARCHAR(7),                   -- 화면 뱃지용 HEX (로고 확장 대비)
    default_commission_rate NUMERIC(5,4)                  -- 0.0680 = 6.8% (확장 대비)
);

-- ----------------------------------------------------------------------------
-- 4. store_platform_connections — 매장:플랫폼 N:M 중간 테이블 (가게 연결 화면)
--    credential_ciphertext: 배민 등 실계정 로그인을 위한 암호화된 자격증명
--    (Fernet, JSON {"login_id", "password"}). 다른 플랫폼(Mock)은 NULL.
-- ----------------------------------------------------------------------------
CREATE TABLE store_platform_connections (
    id                    BIGSERIAL PRIMARY KEY,
    store_id              BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id           INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    platform_store_id     VARCHAR(30) NOT NULL,       -- Mock 스토어 아이디(배민은 실제 shopNo)
    business_number       VARCHAR(20),                -- Mock 사업자번호
    credential_ciphertext TEXT,                       -- 신규. 배민 실계정 암호화 자격증명
    connected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, platform_id)
);

-- ----------------------------------------------------------------------------
-- 5. subscriptions — Basic/Pro 플랜, 하루 답글 생성 한도 (결제 플로우는 범위 외)
-- ----------------------------------------------------------------------------
CREATE TABLE subscriptions (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    plan              VARCHAR(10) NOT NULL DEFAULT 'basic' CHECK (plan IN ('basic', 'pro')),
    daily_reply_limit INT         NOT NULL DEFAULT 10,     -- basic: 하루 답글 생성 한도
    started_at        DATE        NOT NULL,
    expires_at        DATE                                 -- NULL = 무기한 (무료 플랜)
);

-- ----------------------------------------------------------------------------
-- 6. reply_styles — 답글 말투 스타일 마스터 (페르소나 4종)
--    답글 생성은 템플릿 기반 Mock이므로, 별점대별 템플릿을 스타일에 내장한다.
--    플레이스홀더: {nickname}, {menu}, {store}
-- ----------------------------------------------------------------------------
CREATE TABLE reply_styles (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(30)  NOT NULL UNIQUE,   -- 발랄 이모지 파티 / 진중맨 / 무난 요정 / 진지한 하이개그
    description   VARCHAR(200) NOT NULL,          -- 페르소나 설명 (예: 발랄한 20대 여사장님 말투)
    template_high TEXT         NOT NULL,          -- 4~5점 리뷰용 템플릿
    template_mid  TEXT         NOT NULL,          -- 3점 리뷰용 템플릿
    template_low  TEXT         NOT NULL           -- 1~2점 리뷰용 템플릿
);

-- ----------------------------------------------------------------------------
-- 7. reply_settings — 가게별 답글 설정. stores 1:1
-- ----------------------------------------------------------------------------
CREATE TABLE reply_settings (
    id                    BIGSERIAL PRIMARY KEY,
    store_id              BIGINT       NOT NULL UNIQUE REFERENCES stores(id)       ON DELETE CASCADE,
    style_id              INT          NOT NULL        REFERENCES reply_styles(id) ON DELETE RESTRICT,
    promo_text            VARCHAR(400),               -- 답글 끝에 붙는 홍보 문구 (최대 400자)
    include_nickname      BOOLEAN      NOT NULL DEFAULT TRUE,
    include_menu          BOOLEAN      NOT NULL DEFAULT TRUE,
    include_store_name    BOOLEAN      NOT NULL DEFAULT TRUE,
    promo_on_negative     BOOLEAN      NOT NULL DEFAULT FALSE, -- 부정 리뷰에도 홍보 문구 포함 여부
    auto_reply_enabled    BOOLEAN      NOT NULL DEFAULT FALSE, -- 답글 규칙 설정 화면. Mock 뿐 — 실제 자동 등록은 하지 않는다
    auto_reply_min_rating SMALLINT     NOT NULL DEFAULT 1 CHECK (auto_reply_min_rating BETWEEN 1 AND 5)
);

-- ----------------------------------------------------------------------------
-- 8. orders — 주문내역. stores 1:N orders
--    platform_id: 주문내역 화면의 플랫폼 뱃지·플랫폼별 분석용 (합의 사항)
-- ----------------------------------------------------------------------------
CREATE TABLE orders (
    id           BIGSERIAL PRIMARY KEY,
    store_id     BIGINT       NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id  INT          NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    order_no     VARCHAR(30)  NOT NULL UNIQUE,    -- Mock 주문번호 (예: T2ET0001K2T5)
    ordered_at   TIMESTAMPTZ  NOT NULL,
    menu_summary VARCHAR(200) NOT NULL,           -- 주문 메뉴 요약 (예: [20%할인]숯불바베큐두마리 SET)
    order_type   VARCHAR(10)  NOT NULL CHECK (order_type IN ('delivery', 'takeout')),
    amount       INT          NOT NULL CHECK (amount >= 0)   -- 주문금액 (원)
);

CREATE INDEX idx_orders_store_time    ON orders(store_id, ordered_at);
CREATE INDEX idx_orders_platform_time ON orders(platform_id, ordered_at);

-- ----------------------------------------------------------------------------
-- 9. reviews — 리뷰. store_id/platform_id/menu_summary를 직접 갖는다(주문과
--    독립적으로 적재 가능 — 배민 리뷰 API에는 주문과 연결할 공통 키가 없다).
--    order_id는 있으면 연결하는 선택적 FK로 남겨둔다(현재는 채워지는 경우 없음).
--    external_review_id: 배민 리뷰 API의 id. 재동기화 시 중복 판별 키. Mock은 NULL.
--    platform_shop_no: 배민처럼 연결 하나가 여러 매장(브랜드)을 가질 때 어느
--    매장에서 온 리뷰인지 식별. Mock 리뷰나 단일 매장 플랫폼은 NULL.
--    상태: unanswered(미등록) → pending(등록 대기: AI 초안 생성됨) → answered(등록 완료)
-- ----------------------------------------------------------------------------
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
    customer_nickname    VARCHAR(50) NOT NULL,    -- 닉네임만 저장, 실명 아님
    customer_order_count INT         NOT NULL DEFAULT 1,  -- 이 고객의 누적 주문 횟수 (n회 주문 표시)
    status               VARCHAR(12) NOT NULL DEFAULT 'unanswered'
                         CHECK (status IN ('unanswered', 'pending', 'answered')),
    created_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_reviews_status ON reviews(status);
CREATE INDEX idx_reviews_store  ON reviews(store_id);
CREATE INDEX idx_reviews_platform_shop ON reviews(platform_shop_no);

-- ----------------------------------------------------------------------------
-- 10. review_replies — 답글. reviews 1:N
--     ai_draft  = Mock 초안 / final = 사장 최종 답글 / secondary = 답글 완료 리뷰에 붙이는 2차(추가) 답글
-- ----------------------------------------------------------------------------
CREATE TABLE review_replies (
    id         BIGSERIAL PRIMARY KEY,
    review_id  BIGINT      NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    reply_type VARCHAR(10) NOT NULL CHECK (reply_type IN ('ai_draft', 'final', 'secondary')),
    style_id   INT         REFERENCES reply_styles(id) ON DELETE SET NULL,  -- 생성 당시 스타일 (이력 보존)
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_review_replies_review ON review_replies(review_id);

-- ----------------------------------------------------------------------------
-- 11. daily_settlements — 일별 매출과 입금을 함께 저장 (정산 지연 반영)
--     매출 요약 테이블은 따로 두지 않는다. 기간 요약은 이 테이블을 집계한다.
-- ----------------------------------------------------------------------------
CREATE TABLE daily_settlements (
    id             BIGSERIAL PRIMARY KEY,
    store_id       BIGINT NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id    INT    NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    settle_date    DATE   NOT NULL,
    sales_amount   INT    NOT NULL DEFAULT 0 CHECK (sales_amount >= 0),   -- 그날 발생한 매출액 (원)
    deposit_amount INT    NOT NULL DEFAULT 0 CHECK (deposit_amount >= 0), -- 그날 실제 입금된 금액 (원)
    UNIQUE (store_id, platform_id, settle_date)
);

-- ----------------------------------------------------------------------------
-- 12. repurchase_metrics — 날짜별 재주문율 (보정 전 = 당일, 보정 후 = 이전 7일 합산)
--     비율은 반드시 소수 0~1로 저장한다 (25.5% = 0.2550)
-- ----------------------------------------------------------------------------
CREATE TABLE repurchase_metrics (
    id            BIGSERIAL PRIMARY KEY,
    store_id      BIGINT       NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id   INT          NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    metric_date   DATE         NOT NULL,
    new_orders    INT          NOT NULL DEFAULT 0,
    repeat_orders INT          NOT NULL DEFAULT 0,
    rate_raw      NUMERIC(5,4) NOT NULL CHECK (rate_raw      BETWEEN 0 AND 1),  -- 보정 전
    rate_adjusted NUMERIC(5,4) NOT NULL CHECK (rate_adjusted BETWEEN 0 AND 1),  -- 보정 후 (7일 합산)
    UNIQUE (store_id, platform_id, metric_date)
);

-- ----------------------------------------------------------------------------
-- 13. ad_campaigns — 광고 캠페인. stores 1:N
-- ----------------------------------------------------------------------------
CREATE TABLE ad_campaigns (
    id          BIGSERIAL PRIMARY KEY,
    store_id    BIGINT      NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category    VARCHAR(30) NOT NULL,               -- 노출 카테고리 (예: 치킨)
    current_cpc INT         NOT NULL CHECK (current_cpc >= 0),   -- 현재 클릭당 단가 (원)
    target_rank SMALLINT    NOT NULL CHECK (target_rank >= 1),   -- 목표 순위
    status      VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused'))
);

CREATE INDEX idx_ad_campaigns_store ON ad_campaigns(store_id);

-- ----------------------------------------------------------------------------
-- 14. ad_performance_metrics — 일별 광고 성과 원본
--     계산값(CPC·CVR·AOV·ACoS·점수)은 저장하지 않는다 (정규화 원칙, 합의 사항).
--     acos.py가 조회 시 실제 공식으로 계산:
--       CPC = ad_spend ÷ clicks
--       CVR = ad_orders ÷ clicks          (소수 0~1. 18.4% = 0.184)
--       AOV = ad_revenue ÷ ad_orders
--       ACoS(%) = CPC ÷ (CVR × AOV) × 100
-- ----------------------------------------------------------------------------
CREATE TABLE ad_performance_metrics (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
    metric_date DATE   NOT NULL,
    ad_spend    INT    NOT NULL DEFAULT 0 CHECK (ad_spend   >= 0),  -- 광고비 (원)
    clicks      INT    NOT NULL DEFAULT 0 CHECK (clicks     >= 0),  -- 클릭수
    ad_orders   INT    NOT NULL DEFAULT 0 CHECK (ad_orders  >= 0),  -- 광고 경유 주문수
    ad_revenue  INT    NOT NULL DEFAULT 0 CHECK (ad_revenue >= 0),  -- 광고 경유 매출 (원)
    UNIQUE (campaign_id, metric_date)
);

-- ----------------------------------------------------------------------------
-- 15. ad_rank_snapshots — 순위 스냅샷 (창의 기능: 광고 순위 모니터링)
--     두 종류가 한 테이블에 공존한다:
--     (1) 시간별 Mock 스냅샷 (distance_km NULL) — 실제 순위 수집은 하지 않는다.
--         수집됐다고 가정한 결과만 Mock으로 저장.
--     (2) 반경별 실측 스냅샷 (distance_km NOT NULL) — crawler/(Appium 실기기
--         자동화)로 실제 배민 앱을 스크롤해 수집한 결과. 경쟁 가게 CPC는
--         실측할 수 없어 이 종류에는 저장하지 않는다(NULL로 둔다).
--     suggested_cpc: 추천 액션이 raise/lower일 때 제안 CPC ("CPC 700원으로 인상" 표시용)
-- ----------------------------------------------------------------------------
CREATE TABLE ad_rank_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    campaign_id        BIGINT      NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
    snapshot_at        TIMESTAMPTZ NOT NULL,
    current_rank       SMALLINT    NOT NULL CHECK (current_rank >= 1),
    competitor_est_cpc INT         CHECK (competitor_est_cpc >= 0),  -- 경쟁 가게 예상 CPC (Mock, 시간별 스냅샷 전용)
    status             VARCHAR(12) CHECK (status IN ('normal', 'rank_dropped')),
    recommended_action VARCHAR(10) NOT NULL DEFAULT 'keep'
                       CHECK (recommended_action IN ('keep', 'raise_cpc', 'lower_cpc')),
    suggested_cpc      INT         CHECK (suggested_cpc >= 0),
    distance_km        NUMERIC(4,2) CHECK (distance_km >= 0),  -- NULL=시간별 Mock, 값 있으면 반경 실측(crawler/)
    point_label        VARCHAR(20),                            -- '0km', '1.5~2.5km' 등 화면 표시용 (실측 전용)
    total_scanned      SMALLINT    CHECK (total_scanned >= 0), -- 실측 시 스캔된 리스트 항목 수
    ads_above          SMALLINT    CHECK (ads_above >= 0),     -- 실측 시 내 가게보다 위에 있던 광고 개수
    UNIQUE (campaign_id, snapshot_at)
);

-- ----------------------------------------------------------------------------
-- 16. alerts — 알림 (부정 리뷰 / 미답변 / 순위 하락). 발송은 하지 않고 화면 표시만.
-- ----------------------------------------------------------------------------
CREATE TABLE alerts (
    id         BIGSERIAL PRIMARY KEY,
    store_id   BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    alert_type VARCHAR(20)  NOT NULL
               CHECK (alert_type IN ('negative_review', 'unanswered_review', 'rank_drop')),
    message    VARCHAR(300) NOT NULL,
    is_read    BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_alerts_store_unread ON alerts(store_id, is_read);

-- ----------------------------------------------------------------------------
-- 17. social_accounts — 소셜 로그인 연결(카카오 등). users 1:N social_accounts
--     provider 문자열 기반이라 이후 provider(네이버/구글 등)가 늘어도 스키마
--     재변경 없이 행만 추가하면 된다.
-- ----------------------------------------------------------------------------
CREATE TABLE social_accounts (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider          VARCHAR(20) NOT NULL,          -- kakao (향후 naver, google 등)
    provider_user_id  VARCHAR(100) NOT NULL,         -- 카카오 고유 회원번호
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_social_accounts_user ON social_accounts(user_id);

-- ----------------------------------------------------------------------------
-- 18. signup_verifications — 이메일 인증 코드. 회원가입 이메일 인증(Resend 실발송/
--     휴대폰 Mock, 휴대폰 인증은 현재 가입 위자드에서 빠져 있어 미사용)과 비밀번호
--     재설정 인증에 공용으로 쓰인다. users를 참조하지 않는다 — 회원가입은 인증이
--     모두 끝난 뒤에만 계정이 생성되고, 비밀번호 재설정은 기존 계정을 이메일로
--     조회할 뿐 이 테이블에 FK로 묶을 필요가 없다. target은 이메일이면 평문,
--     휴대폰이면 phone_hash와 동일한 SHA-256 해시(전화번호 원문 저장 금지 원칙 유지).
-- ----------------------------------------------------------------------------
CREATE TABLE signup_verifications (
    id         BIGSERIAL PRIMARY KEY,
    target     VARCHAR(255) NOT NULL,
    purpose    VARCHAR(20)  NOT NULL CHECK (purpose IN ('email', 'phone', 'password_reset')),
    code_hash  CHAR(64)     NOT NULL,
    expires_at TIMESTAMPTZ  NOT NULL,
    attempts   INT          NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (target, purpose)
);

-- ----------------------------------------------------------------------------
-- 19. review_sync_jobs — 배민 리뷰 동기화 작업 상태 추적 (가게 연결 화면의
--     "리뷰 동기화" 버튼 → 백그라운드 작업 → 폴링)
-- ----------------------------------------------------------------------------
CREATE TABLE review_sync_jobs (
    id               BIGSERIAL PRIMARY KEY,
    store_id         BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id      INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'success', 'failed')),
    reviews_fetched  INT,
    reviews_inserted INT,
    error_message    TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);

-- ----------------------------------------------------------------------------
-- 20. baemin_shop_brands — 배민 계정 하나에 딸린 여러 브랜드(매장) 목록. 로그인
--     시 발견되는 shopNo/매장명을 저장해 리뷰 관리 화면의 브랜드 선택
--     드롭다운에 쓴다.
-- ----------------------------------------------------------------------------
CREATE TABLE baemin_shop_brands (
    id            BIGSERIAL PRIMARY KEY,
    connection_id BIGINT       NOT NULL REFERENCES store_platform_connections(id) ON DELETE CASCADE,
    shop_no       VARCHAR(20)  NOT NULL,
    shop_name     VARCHAR(200) NOT NULL,
    UNIQUE (connection_id, shop_no)
);

-- ----------------------------------------------------------------------------
-- 21. brand_ad_click_metrics — 브랜드(우리가게클릭 캠페인)별 일별 광고 성과 원본.
--     계산값(CPC·CVR·AOV·ACoS·점수)은 저장하지 않는다 (ad_performance_metrics와
--     같은 정규화 원칙) — acos.py가 조회 시 실제 공식으로 계산한다.
--     ad_campaigns(카테고리 기반, 광고 순위 모니터링용)와는 완전히 별개다.
-- ----------------------------------------------------------------------------
CREATE TABLE brand_ad_click_metrics (
    id          BIGSERIAL PRIMARY KEY,
    store_id    BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    shop_no     VARCHAR(20) NOT NULL,  -- baemin_shop_brands.shop_no와 동일한 값
    metric_date DATE        NOT NULL,
    ad_spend    INT NOT NULL DEFAULT 0 CHECK (ad_spend    >= 0),  -- spentBudget
    impressions INT NOT NULL DEFAULT 0 CHECK (impressions >= 0),  -- displayCount
    clicks      INT NOT NULL DEFAULT 0 CHECK (clicks      >= 0),  -- clickCount
    ad_orders   INT NOT NULL DEFAULT 0 CHECK (ad_orders   >= 0),  -- orderCount
    ad_revenue  INT NOT NULL DEFAULT 0 CHECK (ad_revenue  >= 0),  -- orderAmounts
    UNIQUE (store_id, platform_id, shop_no, metric_date)
);

COMMIT;
