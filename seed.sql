-- ============================================================================
-- Delivery Review & Store Insight MVP — Mock Seed Data
-- ============================================================================
-- 개인정보 없음. 전화번호는 phone_hash(SHA-256), 사업자번호·스토어 아이디·
-- 주문번호는 전부 Mock 값. 매장/메뉴/닉네임은 전부 가상.
--
-- 데모 계정: demo@dris.kr / demo1234!  (password_hash는 bcrypt)
--
-- 생성 전략:
--   주문은 generate_series로 최근 60일치 생성 (setseed로 결정적).
--   daily_settlements는 주문을 집계해서 만들므로 원본과 항상 일치하며,
--   입금액은 D+3 지연 + 수수료 차감으로 계산해 "매출 ≠ 입금" 현장 문제를 재현.
--   재주문율 보정 후 값은 7일 윈도우 합산으로 계산 (원본과 일치).
--   답글은 reply_styles 템플릿 + reply_settings 설정을 실제로 적용해 생성.
-- ============================================================================

BEGIN;

SELECT setseed(0.42);

TRUNCATE alerts, ad_rank_snapshots, ad_performance_metrics, ad_campaigns,
         repurchase_metrics, daily_settlements, review_replies, reviews, orders,
         reply_settings, reply_styles, subscriptions, store_platform_connections,
         platforms, stores, users
RESTART IDENTITY CASCADE;

-- ----------------------------------------------------------------------------
-- 1. users — 데모 사장 1명 (demo@dris.kr / demo1234!)
-- ----------------------------------------------------------------------------
INSERT INTO users (email, password_hash, nickname, phone_hash, marketing_agreed) VALUES
('demo@dris.kr',
 '$2b$12$o1vNfCKgclqaAM.xTiZDuOss4S7Skgi275eY7CORXipRKt7tw3xMi',  -- bcrypt('demo1234!')
 '김사장',
 '8784c90d4a0106d3f67e67a3545103740560bf5347c3d947bb75d1ce81f6c590',  -- sha256('010-0000-0000') Mock
 TRUE);

-- ----------------------------------------------------------------------------
-- 2. stores — 가상 매장 2개
-- ----------------------------------------------------------------------------
INSERT INTO stores (user_id, name, category, address) VALUES
(1, '치킨대장 당고점',   '치킨',   '서울시 노원구 당고개로 1 (Mock)'),
(1, '닭갈비연구소 당고점', '닭갈비', '서울시 노원구 당고개로 2 (Mock)');

-- ----------------------------------------------------------------------------
-- 3. platforms — 플랫폼 마스터 (뱃지 색상, 기본 수수료율)
-- ----------------------------------------------------------------------------
INSERT INTO platforms (code, name, brand_color, default_commission_rate) VALUES
('baemin',       '배달의민족', '#2AC1BC', 0.0680),
('coupang_eats', '쿠팡이츠',   '#F7552F', 0.0980),
('yogiyo',       '요기요',     '#FA0050', 0.1250);

-- ----------------------------------------------------------------------------
-- 4. store_platform_connections — 1호점×3플랫폼, 2호점×배민 (전부 Mock 값)
-- ----------------------------------------------------------------------------
INSERT INTO store_platform_connections (store_id, platform_id, platform_store_id, business_number) VALUES
(1, 1, 'MK-10000001', '000-00-00001'),
(1, 2, 'MK-10000002', '000-00-00001'),
(1, 3, 'MK-10000003', '000-00-00001'),
(2, 1, 'MK-20000001', '000-00-00002');

-- ----------------------------------------------------------------------------
-- 5. subscriptions — 데모 계정은 Basic (하루 답글 생성 10건 한도)
-- ----------------------------------------------------------------------------
INSERT INTO subscriptions (user_id, plan, daily_reply_limit, started_at, expires_at) VALUES
(1, 'basic', 10, CURRENT_DATE - 90, NULL);

-- ----------------------------------------------------------------------------
-- 6. reply_styles — 페르소나 4종 + 별점대별 템플릿 + 불만 리뷰 RAG용 톤 지시문
--    플레이스홀더: {nickname}, {menu}, {store}
-- ----------------------------------------------------------------------------
INSERT INTO reply_styles (name, description, template_high, template_mid, template_low, tone_instruction) VALUES
('이모지 불맛', '이모지를 아낌없이 써서 발랄하고 신나게 답변합니다.',
 '{nickname}님 안녕하세요~ 💙 {menu} 맛있게 드셨다니 저희가 더 행복해요!! 🎉 {store}는 언제나 따끈따끈 준비하고 있을게요~ 또 만나요! 💛',
 '{nickname}님~ 솔직한 후기 감사해요! 🙏 {menu} 조금 아쉬우셨던 부분은 꼭 개선할게요! {store}가 다음엔 더 맛있게 준비할게요 💪',
 '{nickname}님… 속상하셨죠 😢 정말 죄송해요. {menu} 말씀 주신 부분 바로 확인하고 고치겠습니다. {store}에 한 번만 더 기회를 주세요! 🙇‍♀️',
 '이모지를 문장마다 적극적으로 사용하고, 밝고 통통 튀는 말투로 작성하세요.'),
('담백한 손맛', '이모지 없이 꾸밈없고 진중하게, 책임감 있는 말투로 답변합니다.',
 '{nickname}님, {menu}를 맛있게 드셨다니 감사합니다. 고객님의 칭찬이 가게 운영에 큰 힘이 됩니다. {store}는 언제나 최상의 음식을 제공하기 위해 노력하겠습니다.',
 '{nickname}님, 소중한 의견 감사합니다. {menu}에서 부족했던 점을 겸허히 받아들이고 개선하겠습니다. 다시 찾아주신다면 만족하실 수 있도록 하겠습니다.',
 '{nickname}님, 기대에 미치지 못해 진심으로 사과드립니다. {menu} 관련 지적해주신 사항은 즉시 개선하겠습니다. {store} 대표로서 책임지고 바로잡겠습니다.',
 '이모지를 쓰지 않고, 격식 있고 담백한 말투로 신뢰감 있게 작성하세요.'),
('다정한 슴슴함', '이모지를 적당히 섞어 자극적이지 않고 편안하게, 다정한 말투로 답변합니다.',
 '안녕하세요 {nickname}님! 😊 {menu} 맛있게 드셨다니 기분이 좋습니다! {store}를 믿고 찾아주셔서 감사드리며, 다음에도 변함없는 맛으로 보답할게요 💕',
 '{nickname}님, 후기 남겨주셔서 감사해요 😊 {menu}에서 아쉬우셨던 부분은 잘 새겨듣고 개선하겠습니다. 다음엔 꼭 만족시켜드릴게요!',
 '{nickname}님, 불편을 드려 정말 죄송합니다 🙏 {menu} 문제는 바로 확인해서 재발하지 않도록 하겠습니다. {store}가 더 나아진 모습 보여드릴게요.',
 '이모지를 한두 개만 은은하게 섞어, 편안하고 다정한 말투로 작성하세요.'),
('위트있는 칼칼함', '평소엔 재치있고 유쾌하지만, 불만 리뷰에는 장난기를 빼고 진지하게 답변합니다.',
 '{nickname}님이라는 닉네임, 명예의 전당감입니다! {menu} 만들며 돌리는 제 손길이 오늘따라 경쾌했는데 통하셨군요. {store}는 늘 이 자리에서 기다립니다!',
 '{nickname}님! 주방에 특훈 지시 내렸습니다. {menu} 다음 판은 오늘보다 한 수 위로 준비하겠습니다. 기대 반 긴장 반으로 기다려주세요!',
 '{nickname}님, 오늘은 농담을 아끼겠습니다. {menu}로 실망을 드려 죄송합니다. 말씀 주신 부분 진지하게 고치겠습니다. 다음엔 웃으며 뵙고 싶습니다.',
 '가볍고 재치있는 표현을 섞되 과하지 않게, 위트 있는 말투로 작성하세요.');

-- ----------------------------------------------------------------------------
-- 7. reply_settings — 가게별 답글 설정 (1호점: 이모지 불맛, 2호점: 담백한 손맛)
-- ----------------------------------------------------------------------------
INSERT INTO reply_settings (store_id, style_id, promo_text, include_nickname, include_menu, include_store_name, promo_on_negative, auto_reply_enabled, auto_reply_min_rating) VALUES
(1, 1, '📌 매주 화요일은 치즈볼 서비스 데이! 리뷰 이벤트도 진행 중이에요 💙', TRUE, TRUE, TRUE, FALSE, TRUE, 4),
(2, 2, '점심 특선 1인 세트 새로 출시했습니다. 많은 관심 부탁드립니다.', TRUE, TRUE, TRUE, FALSE, FALSE, 1);

-- ----------------------------------------------------------------------------
-- 8. orders — 최근 60일, 연결(매장×플랫폼)마다 하루 1~3건 (약 500건)
-- ----------------------------------------------------------------------------
INSERT INTO orders (store_id, platform_id, order_no, ordered_at, menu_summary, order_type, amount)
SELECT
    c.store_id,
    c.platform_id,
    'MKORD-' || to_char(d.d, 'YYMMDD') || '-' || lpad((row_number() OVER ())::text, 4, '0'),
    d.d + time '11:00'
        + make_interval(mins => floor(random() * 660)::int),          -- 11:00 ~ 21:59
    CASE s.category
      WHEN '치킨' THEN (ARRAY['[20%할인] 숯불바베큐 두마리 SET','양념치킨 + 치즈볼','후라이드 반반 + 콜라 1.25L','간장마늘치킨 순살','매운양념치킨 + 주먹밥'])[1 + floor(random()*5)::int]
      ELSE            (ARRAY['1인 닭갈비 + 볶음밥 SET','치즈닭갈비 2인 세트','매운닭갈비 + 우동사리','닭갈비 덮밥 도시락','막국수 + 닭갈비 반반'])[1 + floor(random()*5)::int]
    END,
    CASE WHEN random() < 0.9 THEN 'delivery' ELSE 'takeout' END,
    (15 + floor(random() * 21)::int) * 1000                            -- 15,000 ~ 35,000원
FROM store_platform_connections c
JOIN stores s ON s.id = c.store_id
CROSS JOIN generate_series(CURRENT_DATE - 60, CURRENT_DATE - 1, interval '1 day') AS d(d)
-- 하루 주문 건수 1~3건: LATERAL 안의 random()은 재평가가 보장되지 않으므로
-- 외부 행에 의존하는 hashtext 기반 결정적 난수를 사용한다
CROSS JOIN LATERAL generate_series(
    1, 1 + abs(hashtext(c.store_id || '-' || c.platform_id || '-' || d.d::date)) % 3
) AS n;

-- ----------------------------------------------------------------------------
-- 9. reviews — 최근 30일 주문 중 40건에 1:1 리뷰
--    별점 분포: 5점 55% / 4점 20% / 3점 10% / 2점 8% / 1점 7%
--    상태: 20건 answered, 2건 pending(초안만 생성됨), 나머지 unanswered
-- ----------------------------------------------------------------------------
INSERT INTO reviews (order_id, store_id, platform_id, menu_summary, rating, content, customer_nickname, customer_order_count, status, created_at)
SELECT
    o.id,
    o.store_id,
    o.platform_id,
    o.menu_summary,
    r.rating,
    CASE
      WHEN r.rating >= 4 THEN (ARRAY['진짜 맛있어요. 재주문 의사 100%입니다','바삭하고 양도 푸짐해요. 최고!','배달도 빠르고 포장도 꼼꼼했어요','잘 먹겠습니다^^ 사진 보세요 비주얼 대박','단골 확정입니다. 늘 한결같아요'])[1 + floor(random()*5)::int]
      WHEN r.rating  = 3 THEN (ARRAY['맛은 있는데 배달이 좀 늦었어요','무난해요. 가끔 시켜먹기 좋아요','양이 조금 줄어든 느낌이네요'])[1 + floor(random()*3)::int]
      ELSE                    (ARRAY['식어서 왔어요. 실망입니다','주문한 거랑 다른 메뉴가 왔어요','소스가 안 왔어요. 확인 부탁드립니다'])[1 + floor(random()*3)::int]
    END,
    (ARRAY['먹보','치킨러버','동네주민','야식왕','리뷰요정','단골손님','오늘은머냐','맛잘알'])[1 + floor(random()*8)::int],
    1 + floor(random() * 8)::int,
    CASE WHEN r.rn <= 20 THEN 'answered' WHEN r.rn <= 22 THEN 'pending' ELSE 'unanswered' END,
    o.ordered_at + make_interval(hours => 1 + floor(random() * 5)::int)
FROM (
    -- 주의: 선택(ORDER BY random())과 별점(random())을 같은 쿼리 레벨에 두면
    -- PostgreSQL이 두 random()을 동일 표현식으로 통합해 상관관계가 생긴다.
    -- MATERIALIZED CTE로 선택을 먼저 고정한 뒤 별점을 따로 뽑는다.
    WITH picked AS MATERIALIZED (
        SELECT id
        FROM orders
        WHERE ordered_at >= CURRENT_DATE - 30
        ORDER BY random()
        LIMIT 40
    )
    SELECT id,
           row_number() OVER (ORDER BY id) AS rn,
           CASE WHEN p < 0.55 THEN 5 WHEN p < 0.75 THEN 4 WHEN p < 0.85 THEN 3 WHEN p < 0.93 THEN 2 ELSE 1 END AS rating
    FROM (SELECT id, random() AS p FROM picked) x
) r
JOIN orders o ON o.id = r.id;

-- ----------------------------------------------------------------------------
-- 10. review_replies — 템플릿 × 가게 설정을 실제로 적용해 생성
--     answered 리뷰: ai_draft + final (사장이 초안을 확정한 흐름)
--     pending 리뷰: ai_draft만 (등록 대기 상태)
-- ----------------------------------------------------------------------------
WITH reply_source AS (
    SELECT
        rv.id AS review_id,
        rv.status,
        rs.style_id,
        replace(replace(replace(
            CASE WHEN rv.rating >= 4 THEN st.template_high
                 WHEN rv.rating  = 3 THEN st.template_mid
                 ELSE st.template_low END,
            '{nickname}', CASE WHEN rs.include_nickname THEN rv.customer_nickname ELSE '고객' END),
            '{menu}',     CASE WHEN rs.include_menu THEN o.menu_summary ELSE '주문하신 메뉴' END),
            '{store}',    CASE WHEN rs.include_store_name THEN s.name ELSE '저희 가게' END)
        || CASE WHEN rs.promo_text IS NOT NULL AND (rv.rating >= 3 OR rs.promo_on_negative)
                THEN E'\n\n' || rs.promo_text ELSE '' END AS content,
        rv.created_at
    FROM reviews rv
    JOIN orders o          ON o.id = rv.order_id
    JOIN stores s          ON s.id = o.store_id
    JOIN reply_settings rs ON rs.store_id = s.id
    JOIN reply_styles st   ON st.id = rs.style_id
    WHERE rv.status IN ('answered', 'pending')
)
INSERT INTO review_replies (review_id, reply_type, style_id, content, created_at)
SELECT review_id, t.reply_type, style_id, content,
       created_at + CASE t.reply_type WHEN 'ai_draft' THEN interval '1 hour' ELSE interval '2 hours' END
FROM reply_source
CROSS JOIN LATERAL (VALUES ('ai_draft'), ('final')) AS t(reply_type)
WHERE t.reply_type = 'ai_draft' OR status = 'answered';

-- ----------------------------------------------------------------------------
-- 11. daily_settlements — 주문을 집계해 생성 (원본과 항상 일치)
--     매출액 = 그날 주문 합계
--     입금액 = 3일 전 매출 × (1 - 중개수수료율 - 결제수수료 3%) : D+3 정산 지연 가정
-- ----------------------------------------------------------------------------
WITH daily AS (
    SELECT store_id, platform_id, ordered_at::date AS d, SUM(amount)::int AS sales
    FROM orders
    GROUP BY store_id, platform_id, ordered_at::date
)
INSERT INTO daily_settlements (store_id, platform_id, settle_date, sales_amount, deposit_amount)
SELECT
    d1.store_id, d1.platform_id, d1.d, d1.sales,
    COALESCE(round(d0.sales * (1 - p.default_commission_rate - 0.03))::int, 0)
FROM daily d1
JOIN platforms p ON p.id = d1.platform_id
LEFT JOIN daily d0
       ON d0.store_id = d1.store_id AND d0.platform_id = d1.platform_id
      AND d0.d = d1.d - 3;

-- ----------------------------------------------------------------------------
-- 12. repurchase_metrics — 최근 30일 × (1호점 배민, 2호점 배민)
--     보정 전 = 당일 비율, 보정 후 = 이전 7일 합산 비율 (윈도우 계산, 원본과 일치)
-- ----------------------------------------------------------------------------
WITH base AS (
    SELECT s.store_id, 1 AS platform_id, d.d::date AS md,
           (12 + floor(random() * 10))::int AS new_orders,
           (2 + floor(random() * 6))::int  AS repeat_orders
    FROM (VALUES (1), (2)) AS s(store_id)
    CROSS JOIN generate_series(CURRENT_DATE - 30, CURRENT_DATE - 1, interval '1 day') AS d(d)
),
win AS (
    SELECT *,
           SUM(new_orders)    OVER w AS n7,
           SUM(repeat_orders) OVER w AS r7
    FROM base
    WINDOW w AS (PARTITION BY store_id ORDER BY md ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
)
INSERT INTO repurchase_metrics (store_id, platform_id, metric_date, new_orders, repeat_orders, rate_raw, rate_adjusted)
SELECT store_id, platform_id, md, new_orders, repeat_orders,
       round(repeat_orders::numeric / NULLIF(new_orders + repeat_orders, 0), 4),
       round(r7::numeric            / NULLIF(n7 + r7, 0),                   4)
FROM win;

-- ----------------------------------------------------------------------------
-- 13. ad_campaigns — 캠페인 2개
-- ----------------------------------------------------------------------------
INSERT INTO ad_campaigns (store_id, category, current_cpc, target_rank, status, shop_no) VALUES
(1, '치킨',       400, 3,  'active', '14804318'),
(1, '찜·탕·찌개',  100, 10, 'active', '14804912'),
(1, '고기·구이',   100, 10, 'active', '14804914'),
(1, '백반·죽·국수', 100, 10, 'active', '14805005'),
(2, '닭갈비',      300, 5,  'active', NULL);

-- ----------------------------------------------------------------------------
-- 14. ad_performance_metrics — 최근 14일 원본 성과
--     캠페인1: CVR ≈ 0.17, AOV ≈ 25,000 → ACoS 한 자릿수 (양호)
--     캠페인2: CVR ≈ 0.06, AOV ≈ 20,000 → ACoS 20%대 후반 (개선 필요 구간 포함)
--     ※ ACoS·점수는 저장하지 않는다. acos.py가 조회 시 실제 공식으로 계산.
--     ※ ACoS는 CPC/CVR/AOV "비율"에만 좌우되므로 clicks 절대량은 자유롭게 줄여도 무관하다.
--       ad_orders는 반드시 같은 매장의 실제 주문(orders) 총량보다 작아야
--       "우가클 주문 비중"(ad_orders÷전체 주문)이 100%를 넘지 않는다 — store_platform_connections당
--       하루 1~3건인 orders 시드 규모에 맞춰 clicks를 작게 잡는다.
-- ----------------------------------------------------------------------------
INSERT INTO ad_performance_metrics (campaign_id, metric_date, ad_spend, clicks, ad_orders, ad_revenue)
SELECT c.id, d.d::date,
       cl.clicks * CASE c.id WHEN 1 THEN 165 + floor(random() * 20)::int ELSE 290 + floor(random() * 25)::int END,
       cl.clicks,
       ao.ad_orders,
       ao.ad_orders * CASE c.id WHEN 1 THEN 23000 + floor(random() * 4000)::int ELSE 18000 + floor(random() * 4000)::int END
FROM ad_campaigns c
CROSS JOIN generate_series(CURRENT_DATE - 14, CURRENT_DATE - 1, interval '1 day') AS d(d)
CROSS JOIN LATERAL (
    -- 캠페인2는 CVR이 낮아(0.04~0.09) clicks가 너무 작으면 반올림이 항상 최소 1건으로
    -- 뭉개져 의도한 CVR을 못 낸다 — clicks를 더 크게 잡아 반올림 왜곡을 피한다.
    SELECT CASE c.id WHEN 1 THEN 6 + floor(random() * 6)::int ELSE 15 + floor(random() * 10)::int END AS clicks
) cl
CROSS JOIN LATERAL (
    SELECT greatest(1, round(cl.clicks * CASE c.id WHEN 1 THEN 0.15 + random() * 0.06 ELSE 0.04 + random() * 0.05 END)::int) AS ad_orders
) ao;

-- ----------------------------------------------------------------------------
-- 15. ad_rank_snapshots — 캠페인당 10분 간격 30개 (최근 5시간)
--     캠페인1: 경쟁 CPC 인상으로 3위 → 7위로 밀리는 구간 포함 (창의 기능 데모)
--     캠페인2: 1~2위 안정권
-- ----------------------------------------------------------------------------
INSERT INTO ad_rank_snapshots (campaign_id, snapshot_at, current_rank, competitor_est_cpc, status, recommended_action, suggested_cpc)
SELECT 1,
       date_trunc('minute', now()) - interval '5 hours' + make_interval(mins => i * 10),
       CASE WHEN i < 10 THEN 3 WHEN i < 14 THEN 3 + (i - 9) ELSE 7 END,
       CASE WHEN i < 10 THEN 390 ELSE 650 END,
       CASE WHEN i < 10 THEN 'normal' ELSE 'rank_dropped' END,
       CASE WHEN i < 10 THEN 'keep' ELSE 'raise_cpc' END,
       CASE WHEN i < 10 THEN NULL ELSE 700 END          -- 경쟁 CPC 650 + 50원
FROM generate_series(0, 29) AS i;

INSERT INTO ad_rank_snapshots (campaign_id, snapshot_at, current_rank, competitor_est_cpc, status, recommended_action, suggested_cpc)
SELECT 2,
       date_trunc('minute', now()) - interval '5 hours' + make_interval(mins => i * 10),
       CASE WHEN i % 3 = 0 THEN 2 ELSE 1 END,
       280,
       'normal', 'keep', NULL
FROM generate_series(0, 29) AS i;

-- 캠페인1의 반경별 실측 스냅샷(distance_km IS NOT NULL)은 seed로 심지 않는다 — 이 값은
-- crawler/가 실제 배민 앱을 조작해 측정한 결과만 들어가야 하는 자리라, 가짜 값을 심으면
-- "우리가게 순위 확인"을 한 번도 안 돌린 새 환경에서도 실측인 것처럼 보이는 문제가 생긴다.
-- 실측 전에는 GET /ads/rank-monitoring이 current_rank/rank_status를 null로 반환하는 게 맞는 동작.

-- ----------------------------------------------------------------------------
-- 16. alerts — 데이터에서 파생되는 알림 (발송 없음, 화면 표시용)
-- ----------------------------------------------------------------------------
-- 부정 리뷰(1~2점) 발생 알림: 리뷰 데이터에서 직접 생성
INSERT INTO alerts (store_id, alert_type, message, is_read, created_at)
SELECT o.store_id, 'negative_review',
       format('%s점 부정 리뷰가 등록되었습니다: "%s"', rv.rating, left(rv.content, 30)),
       FALSE, rv.created_at
FROM reviews rv JOIN orders o ON o.id = rv.order_id
WHERE rv.rating <= 2;

-- 미답변 리뷰 건수 알림: 매장별 집계에서 생성
INSERT INTO alerts (store_id, alert_type, message, is_read, created_at)
SELECT o.store_id, 'unanswered_review',
       format('답글을 기다리는 리뷰가 %s건 있습니다', count(*)),
       FALSE, now() - interval '1 hour'
FROM reviews rv JOIN orders o ON o.id = rv.order_id
WHERE rv.status = 'unanswered'
GROUP BY o.store_id;

-- 순위 하락 알림: 캠페인1의 밀림 시점에 생성
INSERT INTO alerts (store_id, alert_type, message, is_read, created_at)
SELECT c.store_id, 'rank_drop',
       format('"%s" 광고 순위가 목표 %s위에서 7위로 하락했습니다. 경쟁 가게 CPC 인상 추정', c.category, c.target_rank),
       FALSE, date_trunc('minute', now()) - interval '5 hours' + interval '100 minutes'
FROM ad_campaigns c WHERE c.id = 1;

COMMIT;
