# 배민 우리가게클릭 브랜드별 실데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드 "우가클 점수" 카드가 브랜드(shop_no) 선택 드롭다운을 통해 실제 배민 "우리가게클릭" 광고 성과(노출/클릭/주문/광고비/광고매출)를 보여주도록 만든다.

**Architecture:** 기존 배민 인증 세션(`backend/scrapers/baemin_auth.py`의 `login()`)과 "데이터 동기화" 작업(`backend/app/review_sync.py`의 `_run_sync`)을 그대로 재사용한다. 새 스크래퍼 모듈(`backend/scrapers/baemin_ads.py`)이 브랜드별 "마케팅 성과 → 우리가게클릭" 화면에서 organic API 응답(`/v2/statistics/campaign/cpc/metrics/{shopNumber}`)을 가로채 새 테이블 `brand_ad_click_metrics`에 upsert한다. 계산값(CPC/CVR/AOV/ACoS/점수)은 저장하지 않고 기존 `acos.py`의 `calculate_performance`를 조회 시점에 그대로 재사용한다. 기존 `ad_campaigns`/`ad_rank_snapshots`(카테고리 기반 광고 순위 모니터링)는 전혀 건드리지 않는다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API), Next.js App Router.

## Global Constraints

- 배민만. 쿠팡이츠/요기요는 범위 밖.
- 브랜드(shop_no) 단위로 완전히 새로운 테이블·API를 만든다 — 매출/입금/재주문율처럼 계정 전체로 합산하지 않는다.
- 기존 `ad_campaigns`(카테고리 기반)/`ad_performance_metrics`/`ad_rank_snapshots`는 스키마·데이터·엔드포인트 무엇도 건드리지 않는다. "광고 순위 모니터링" 기능은 완전히 별개로 그대로 둔다.
- 새 테이블 `brand_ad_click_metrics`는 원본 숫자(`ad_spend`, `impressions`, `clicks`, `ad_orders`, `ad_revenue`)만 저장한다 — CPC/CVR/AOV/ACoS/점수는 저장하지 않는다(기존 `ad_performance_metrics`와 같은 정규화 원칙).
- 백필 범위는 이번 달 포함 최근 3개월(매출/입금 연동과 같은 폭).
- "우가클 주문 비중"(order_share)은 이번 범위에서 만들지 않는다 — 분모(전체 주문수)가 브랜드별로 안 나뉘어 있어 왜곡된다.
- 새 엔드포인트를 만들되(`GET /ads/click-performance`) 기존 "데이터 동기화" 작업/버튼은 그대로 재사용한다 — 새 동기화 버튼을 만들지 않는다.
- 로그인 세션 쿠키 + 동적 서명 헤더가 필요한 배민 API라 직접 HTTP 호출은 403/CORS로 막힌다 — 반드시 인증된 `page`가 실제 화면을 이동하며 organic하게 발생시키는 응답을 `page.on("response", ...)`로 가로챈다.
- `fetch_brand_click_metrics`(Playwright 화면 조작)는 자동화된 pytest로 덮지 않는다 — 이미 실 계정으로 4개 브랜드 × 2개월(8월/6월) 라이브 재현 검증을 마쳤다(아래 각 스텝에 반영). 순수 매핑 함수와 `_run_sync` 오케스트레이션 로직만 pytest로 촘촘히 테스트한다. 자격증명은 환경변수로만 다루고 로그에 남기지 않는다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql` + `backend/app/models.py`가 1:1로 맞아야 하는 DB 정본이다.
- 참고 스펙: `docs/superpowers/specs/2026-08-12-baemin-brand-ad-click-performance-design.md`

---

### Task 1: 데이터 모델 — `brand_ad_click_metrics` 테이블 추가

**Files:**
- Modify: `schema.sql` (DROP TABLE 목록, 헤더 테이블 개수, 새 테이블 블록)
- Modify: `backend/app/models.py` (헤더 테이블 개수, 새 `BrandAdClickMetric` 모델)
- Test: `backend/tests/test_brand_ad_click_metrics_model.py` (신규)

**Interfaces:**
- Consumes: 없음.
- Produces: `BrandAdClickMetric` 모델(`id, store_id, platform_id, shop_no: str, metric_date: date, ad_spend, impressions, clicks, ad_orders, ad_revenue`). 이후 모든 태스크가 이 모델을 그대로 쓴다.

- [ ] **Step 1: `schema.sql` — DROP TABLE 목록과 헤더 테이블 개수 갱신**

`schema.sql:4`의 `-- 20개 테이블. 모든 FK에 ON DELETE 정책 명시.`를 다음으로 교체:

```sql
-- 21개 테이블. 모든 FK에 ON DELETE 정책 명시.
```

`schema.sql:21-26`(`DROP TABLE IF EXISTS ...` 블록)을 다음으로 교체:

```sql
DROP TABLE IF EXISTS
    brand_ad_click_metrics, baemin_shop_brands, review_sync_jobs, signup_verifications, social_accounts, alerts, ad_rank_snapshots,
    ad_performance_metrics, ad_campaigns, repurchase_metrics, daily_settlements, review_replies,
    reviews, orders, reply_settings, reply_styles, subscriptions, store_platform_connections,
    platforms, stores, users
CASCADE;
```

- [ ] **Step 2: `schema.sql` — 새 테이블 `brand_ad_click_metrics` 추가**

파일 맨 끝(`baemin_shop_brands` 블록 다음, `COMMIT;` 앞)에 삽입:

```sql

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
```

`tail -5 schema.sql`로 파일이 여전히 `COMMIT;`으로 끝나는지 확인한다.

- [ ] **Step 3: `backend/app/models.py` — 헤더 테이블 개수 갱신**

`backend/app/models.py:1`의 `"""SQLAlchemy 모델 — schema.sql의 20개 테이블과 1:1 대응.`를 다음으로 교체:

```python
"""SQLAlchemy 모델 — schema.sql의 21개 테이블과 1:1 대응.
```

- [ ] **Step 4: 실패하는 테스트 작성**

`backend/tests/test_brand_ad_click_metrics_model.py` 신규 생성:

```python
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import BrandAdClickMetric


def test_brand_ad_click_metric_round_trips(db_session, seeded_user, platforms):
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.commit()

    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
    ).one()
    assert row.ad_spend == 95
    assert row.impressions == 40
    assert row.clicks == 1


def test_brand_ad_click_metric_unique_constraint_blocks_duplicate_key(db_session, seeded_user, platforms):
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.commit()

    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=999, impressions=999, clicks=9, ad_orders=9, ad_revenue=9000,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 5: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_brand_ad_click_metrics_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'BrandAdClickMetric' from 'app.models'`

- [ ] **Step 6: `backend/app/models.py`에 `BrandAdClickMetric` 모델 추가**

`class AdPerformanceMetric` 블록(`ad_revenue: Mapped[int] = mapped_column(default=0)`로 끝나는 블록) 다음, `class AdRankSnapshot(Base):` 앞에 삽입:

```python


class BrandAdClickMetric(Base):
    __tablename__ = "brand_ad_click_metrics"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    shop_no: Mapped[str] = mapped_column(String(20))
    metric_date: Mapped[date]
    ad_spend: Mapped[int] = mapped_column(default=0)
    impressions: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    ad_orders: Mapped[int] = mapped_column(default=0)
    ad_revenue: Mapped[int] = mapped_column(default=0)
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_brand_ad_click_metrics_model.py -v`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 8: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 9: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_brand_ad_click_metrics_model.py
git commit -m "feat: 브랜드별 우리가게클릭 광고 성과 원본을 저장할 brand_ad_click_metrics 테이블 추가"
```

---

### Task 2: 순수 매핑 함수 — CPC 성과 API 응답 → 날짜별 집계

**Files:**
- Create: `backend/scrapers/baemin_ads.py`
- Test: `backend/tests/test_baemin_ads.py`

**Interfaces:**
- Consumes: 없음(외부 의존 없는 순수 함수).
- Produces: `map_click_metrics_by_date(responses: list[dict]) -> dict[str, dict]`(키: `ad_spend`, `impressions`, `clicks`, `ad_orders`, `ad_revenue`). Task 4가 이 함수를 그대로 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_baemin_ads.py` 신규 생성:

```python
from scrapers.baemin_ads import map_click_metrics_by_date

# 실 계정(치밥대장, 2026-08-12 조사)에서 확인한 실제
# /v2/statistics/campaign/cpc/metrics/{shopNumber} 응답 형태.
_AUGUST_RESPONSE = {
    "summary": {"displayCount": 201, "clickCount": 6, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 2.985, "orderRate": 0.0, "spentBudget": 570, "returnOnAdSpend": 0.0},
    "metrics": {"displayCount": [], "clickCount": [], "orderCount": [], "orderAmounts": []},
    "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 95, "displayCount": 40, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
        {"date": "2026-08-02", "spentBudget": 95, "displayCount": 12, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
    ],
}
_JUNE_RESPONSE = {
    "summary": {"displayCount": 649, "clickCount": 31, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 4.776, "orderRate": 0.0, "spentBudget": 2945, "returnOnAdSpend": 0.0},
    "metrics": {"displayCount": [], "clickCount": [], "orderCount": [], "orderAmounts": []},
    "dailyMetrics": [
        {"date": "2026-06-01", "spentBudget": 100, "displayCount": 20, "clickCount": 1,
         "orderCount": 1, "orderAmounts": 15000, "returnOnAdSpend": 150.0},
    ],
}


def test_map_click_metrics_by_date_extracts_daily_fields():
    result = map_click_metrics_by_date([_AUGUST_RESPONSE])
    assert result == {
        "2026-08-01": {"ad_spend": 95, "impressions": 40, "clicks": 1, "ad_orders": 0, "ad_revenue": 0},
        "2026-08-02": {"ad_spend": 95, "impressions": 12, "clicks": 1, "ad_orders": 0, "ad_revenue": 0},
    }


def test_map_click_metrics_by_date_merges_multiple_months_no_overlap():
    result = map_click_metrics_by_date([_AUGUST_RESPONSE, _JUNE_RESPONSE])
    assert set(result.keys()) == {"2026-08-01", "2026-08-02", "2026-06-01"}
    assert result["2026-06-01"] == {
        "ad_spend": 100, "impressions": 20, "clicks": 1, "ad_orders": 1, "ad_revenue": 15000,
    }


def test_map_click_metrics_by_date_empty_list_returns_empty_dict():
    assert map_click_metrics_by_date([]) == {}


def test_map_click_metrics_by_date_response_with_no_daily_metrics_key_contributes_nothing():
    # 캠페인이 없는 브랜드 등, 방어적으로 dailyMetrics가 없는 응답이 섞여도
    # 죽지 않고 그 응답만 건너뛴다.
    assert map_click_metrics_by_date([{"summary": {}, "metrics": {}}]) == {}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_ads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.baemin_ads'`

- [ ] **Step 3: `backend/scrapers/baemin_ads.py` 구현**

```python
"""배민 사장님광장의 "마케팅 성과 → 우리가게클릭" 화면(브랜드별 광고 클릭
성과) API 응답을 날짜별로 집계하는 순수 함수와, 그 organic 응답을 실제로
캡처하는 `fetch_brand_click_metrics`.

기존 `baemin_stats.py`(가게통계/정산내역/주문내역 — 계정 전체 합산 지표)와
관심사가 달라 별도 파일로 분리했다: 이 모듈이 다루는 "우리가게클릭"은
브랜드(shop_no) 단위로만 조회되는 화면이고, 계정 전체로 합산하지 않는다
(설계 문서의 "스코프 결정" 절 참고).
"""


def map_click_metrics_by_date(responses: list[dict]) -> dict[str, dict]:
    """`GET /v2/statistics/campaign/cpc/metrics/{shopNumber}` 응답들의
    `dailyMetrics[].{date, spentBudget, displayCount, clickCount, orderCount,
    orderAmounts}`를 날짜별 dict로 모은다. 브랜드 하나에 대해 여러 달을
    호출한 응답을 전부 이 리스트에 담아 넘긴다 — 달마다 서로 다른 날짜를
    다루므로 겹칠 일이 없다(같은 달을 두 번 호출하지 않는 한)."""
    result: dict[str, dict] = {}
    for resp in responses:
        for day in resp.get("dailyMetrics", []):
            result[day["date"]] = {
                "ad_spend": day["spentBudget"],
                "impressions": day["displayCount"],
                "clicks": day["clickCount"],
                "ad_orders": day["orderCount"],
                "ad_revenue": day["orderAmounts"],
            }
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_ads.py -v`
Expected: 4개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/scrapers/baemin_ads.py backend/tests/test_baemin_ads.py
git commit -m "feat: 우리가게클릭 CPC 성과 API 응답을 날짜별로 집계하는 순수 매핑 함수 추가"
```

---

### Task 3: 브랜드별 "우리가게클릭" 성과 organic 응답 캡처

**Files:**
- Modify: `backend/scrapers/baemin_ads.py` (Task 2에서 만든 파일에 fetch 함수 추가)

**Interfaces:**
- Consumes: Task 2의 `map_click_metrics_by_date`는 쓰지 않는다(호출자인 Task 4가 이 함수의 반환값을 그대로 Task 2 함수에 넘긴다). `baemin_auth.login()`이 반환한 `BaeminSession.page`(이미 인증된 살아있는 Playwright 페이지)를 그대로 받는다 — 재로그인하지 않는다.
- Produces: `fetch_brand_click_metrics(page, shop_no: int, months: list[str]) -> list[dict]`(반환: raw 응답 dict 리스트, `map_click_metrics_by_date`에 그대로 넘길 수 있다), `BaeminAdsScrapeError`. Task 4가 이 함수를 그대로 가져다 쓴다.

이 태스크는 이미 계획 수립 과정에서 실 계정으로 라이브 조사를 마쳤다 — 리뷰/매출 스크래핑 때와 달리 Step 1의 "조사"가 아니라 "이미 확정된 조사 결과를 코드로 옮기는" 태스크다. 확인된 사실(설계 문서 "조사 과정에서 확인된 사실" 절과 동일):

- 화면 URL은 브랜드(shop_no) 단위로 직접 이동 가능하다 — `https://self.baemin.com/shops/{shopNumber}/stat/marketing/woori-shop-click`. 계정에 연결된 4개 브랜드 전부 캠페인 목록을 거치지 않고 이 URL로 바로 진입해서 정상적으로 데이터를 확인했다(2026-08-12 실측 — 4개 브랜드 전부 우리가게클릭 캠페인이 있었다).
- 페이지 로드 직후 콘텐츠가 스켈레톤(회색 placeholder) 상태로 몇 초간 유지된다 — "N월"(현재 선택된 달) 텍스트가 나타날 때까지 폴링 대기가 필요하다(고정 대기만으로는 부족할 수 있다, 실측: 첫 로드 후 최대 5초 추가 대기가 필요했다).
- 월 선택은 화면에 보이는 "N월" 텍스트(현재 선택된 달, 달마다 라벨이 바뀐다)를 클릭하면 "기간" 다이얼로그가 열리고, 그 안에 **네이티브 `<select>`**(정산내역의 캘린더 다이얼로그와 다르다)가 있다 — `option value`가 `"YYYY-MM"` 형식이고 최근 12개월(이번 달 포함)이 들어있다. `select_option(value="YYYY-MM")` 후 "적용" 버튼을 누르면 그 즉시 새 organic 응답이 발생한다(실측 확인 — 6월 선택 시 `startDate=2026-06-01&endDate=2026-06-30` 요청이 새로 나가고 실제 6월 데이터를 반환했다).
- `dailyMetrics`는 그 달 전체 일수가 한 응답에 이미 다 들어있다 — 페이지네이션이 없다(주문내역과 다르다).
- 다른 배민 화면과 마찬가지로 프로모션 backdrop이 뜰 수 있다 — 페이지 진입 직후에만 방어적으로 닫는다(다이얼로그를 여는 클릭들 사이에는 호출하지 않는다 — 방금 연 다이얼로그를 스스로 닫아버리는 기존에 발견된 버그 패턴을 반복하지 않는다).

- [ ] **Step 1: `backend/scrapers/baemin_ads.py`에 fetch 함수 추가**

파일 맨 위(기존 `map_click_metrics_by_date` 앞)에 다음 import를 추가하고, 파일 끝에 아래 함수들을 추가한다:

```python
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
```

파일 끝에 추가:

```python


class BaeminAdsScrapeError(Exception):
    pass


def _dismiss_backdrop_if_present(page) -> None:
    if page.get_by_test_id("backdrop").count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def _select_click_metrics_month(page, month: str) -> None:
    """화면에 보이는 "N월"(현재 선택된 달) 라벨을 클릭해 "기간" 다이얼로그를
    열고, 그 안의 네이티브 `<select>`로 `month`("YYYY-MM")를 고른 뒤 "적용"을
    누른다. 라벨 텍스트는 현재 선택된 달에 따라 바뀌므로("8월", "6월" 등)
    정규식으로 "N월" 형태를 찾는다."""
    import re

    month_label = page.get_by_text(re.compile(r"^\d+월$"), exact=True)
    month_label.first.click(timeout=5_000)
    page.wait_for_timeout(1_000)

    select_el = page.locator("select").last
    select_el.select_option(value=month, timeout=5_000)
    page.wait_for_timeout(500)

    apply_btn = page.get_by_role("button", name="적용")
    apply_btn.first.click(timeout=5_000)
    page.wait_for_timeout(2_000)


def fetch_brand_click_metrics(page, shop_no: int, months: list[str]) -> list[dict]:
    """브랜드(shop_no)의 "마케팅 성과 → 우리가게클릭" 화면에서
    `/v2/statistics/campaign/cpc/metrics/{shopNumber}` organic 응답을
    `months`에 담긴 각 달마다 캡처한다. 계정 전체가 아니라 브랜드 하나에
    대해서만 호출한다 — 호출자가 `session.shops`를 순회하며 브랜드마다
    이 함수를 부른다."""
    responses: list[dict] = []
    observed = {"any": False}

    def _on_response(response) -> None:
        if urlparse(response.url).path != f"/v2/statistics/campaign/cpc/metrics/{shop_no}":
            return
        observed["any"] = True
        if response.status == 200:
            try:
                responses.append(response.json())
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/stat/marketing/woori-shop-click")
        except Exception as e:
            raise BaeminAdsScrapeError(f"우리가게클릭 성과 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(4_000)
        _dismiss_backdrop_if_present(page)

        # 콘텐츠가 스켈레톤 상태로 몇 초간 유지될 수 있어, "N월" 라벨이
        # 실제로 나타날 때까지 최대 15초 폴링한다(고정 대기만으로는 첫
        # 로드 시점에 따라 부족할 수 있음을 실측으로 확인했다).
        import re
        for _ in range(15):
            if page.get_by_text(re.compile(r"^\d+월$"), exact=True).count() > 0:
                break
            page.wait_for_timeout(1_000)

        for month in months:
            try:
                _select_click_metrics_month(page, month)
            except PlaywrightTimeoutError as e:
                raise BaeminAdsScrapeError(f"{month} 우리가게클릭 성과 조회 중 월 선택에 실패했습니다: {e}") from e
    finally:
        page.remove_listener("response", _on_response)

    if not observed["any"]:
        raise BaeminAdsScrapeError("우리가게클릭 성과 API 응답을 한 번도 확인하지 못했습니다")

    return responses
```

- [ ] **Step 2: 실 계정으로 동작 재검증**

이 태스크의 Global Constraints에 따라 화면 상호작용 자체는 자동화된 pytest로 덮지 않는다 — 이미 계획 수립 과정에서 이 정확한 흐름(4개 브랜드 × URL 직접 진입, 8월 조회, 6월로 변경 조회)을 라이브로 검증했지만, 위 Step 1에서 그 결과를 함수로 옮기는 과정에서 오타/누락이 있을 수 있으므로 실 계정으로 최종 확인한다:

```bash
cd backend
.venv/bin/python -c "
import os
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:15432/delivery_insight')
os.environ.setdefault('CREDENTIAL_ENCRYPTION_KEY', '<로컬 검증용 .env의 CREDENTIAL_ENCRYPTION_KEY 값>')
from sqlalchemy import create_engine, text
from app.credential_crypto import decrypt_credential
from scrapers.baemin_auth import login
from scrapers.baemin_ads import fetch_brand_click_metrics

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    ciphertext = conn.execute(text(\"select credential_ciphertext from store_platform_connections where platform_id = (select id from platforms where code='baemin') limit 1\")).one()[0]
cred = decrypt_credential(ciphertext)

session = login(cred['login_id'], cred['password'])
shop_no = session.shops[0][0]
responses = fetch_brand_click_metrics(session.page, shop_no, ['2026-06', '2026-08'])
print('응답 개수:', len(responses))
for r in responses:
    print(r.get('summary'))
session.close()
"
```
Expected: `응답 개수: 2`, 각 summary에 실제 노출수/클릭수/광고비 등이 0이 아닌 값으로(또는 그 달 실제 활동이 없었다면 0으로) 찍힌다. 실패하면 Step 1의 선택자를 다시 점검한다 — 특히 `_select_click_metrics_month`의 "N월" 라벨 정규식과 네이티브 select 여부.

- [ ] **Step 3: 전체 백엔드 테스트 재확인 (회귀 없음 확인용 — 이 태스크는 새 자동 테스트를 추가하지 않는다)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/scrapers/baemin_ads.py
git commit -m "feat: 브랜드별 우리가게클릭 성과 화면의 organic 응답을 가로채는 fetch 함수 추가"
```

---

### Task 4: `review_sync.py` 통합 — 브랜드별 upsert + "데이터 동기화"에 편입

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 1의 `BrandAdClickMetric` 모델. Task 2의 `map_click_metrics_by_date`. Task 3의 `fetch_brand_click_metrics`/`BaeminAdsScrapeError`. 기존 `backend/scrapers/baemin_stats.py`의 `recent_months`(이미 있음, 새로 안 만듦).
- Produces: `upsert_brand_ad_click_metric(db: Session, store_id: int, platform_id: int, shop_no: str, metric_date: str, *, ad_spend: int, impressions: int, clicks: int, ad_orders: int, ad_revenue: int) -> None`. `_run_sync`가 내부적으로 호출한다 — 다른 태스크가 직접 소비하지는 않는다.

- [ ] **Step 1: `upsert_brand_ad_click_metric`에 대한 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 상단 import 블록에 다음을 추가(기존 import 유지):

```python
from app.models import BrandAdClickMetric
from app.review_sync import upsert_brand_ad_click_metric
```

파일 끝에 추가:

```python
def test_upsert_brand_ad_click_metric_creates_new_row(db_session, seeded_user, platforms):
    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95
    assert row.impressions == 40


def test_upsert_brand_ad_click_metric_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    from datetime import date
    existing = BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date.fromisoformat("2026-08-01"),
        ad_spend=999, impressions=999, clicks=9, ad_orders=9, ad_revenue=9000,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    rows = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date="2026-08-01",
    ).all()
    assert len(rows) == 1
    assert rows[0].ad_spend == 95


def test_upsert_brand_ad_click_metric_leaves_other_brand_rows_untouched(db_session, seeded_user, platforms):
    from datetime import date
    other_brand = BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date=date.fromisoformat("2026-08-01"),
        ad_spend=285, impressions=40, clicks=3, ad_orders=1, ad_revenue=19900,
    )
    db_session.add(other_brand)
    db_session.commit()

    upsert_brand_ad_click_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "14804912", "2026-08-01",
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    )
    db_session.commit()

    untouched = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date="2026-08-01",
    ).one()
    assert untouched.ad_spend == 285  # 안 건드림
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_brand_ad_click_metric`
Expected: FAIL — `ImportError: cannot import name 'upsert_brand_ad_click_metric'`

- [ ] **Step 3: `backend/app/review_sync.py`에 `upsert_brand_ad_click_metric` 추가**

파일 상단 import 블록을 다음으로 교체(기존 항목 유지 + 신규 항목 추가):

```python
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.credential_crypto import CredentialCryptoError, decrypt_credential
from app.db import SessionLocal
from app.models import (
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
from scrapers.baemin_ads import BaeminAdsScrapeError, fetch_brand_click_metrics, map_click_metrics_by_date
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, extract_owner_reply, fetch_all_reviews, map_review
from scrapers.baemin_stats import (
    BaeminStatsScrapeError,
    compute_repurchase_rates,
    fetch_account_settlement,
    fetch_current_month_orders,
    fetch_shop_stats,
    map_deposits_by_date,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    recent_months,
)
```

`upsert_repurchase_metric` 함수 다음, `sync_reviews_for_job` 함수 앞에 삽입:

```python
def upsert_brand_ad_click_metric(
    db: Session, store_id: int, platform_id: int, shop_no: str, metric_date: str,
    *, ad_spend: int, impressions: int, clicks: int, ad_orders: int, ad_revenue: int,
) -> None:
    """`(store_id, platform_id, shop_no, metric_date)` 기준 upsert. 계정
    전체 합산인 `upsert_daily_settlement`와 달리 브랜드(shop_no)까지
    키에 포함한다 — 우리가게클릭은 애초에 브랜드 단위로만 조회되는
    화면이라 계정 전체로 합산할 이유가 없다(설계 문서 스코프 결정 참고)."""
    d = date.fromisoformat(metric_date)
    existing = db.scalar(
        select(BrandAdClickMetric).where(
            BrandAdClickMetric.store_id == store_id,
            BrandAdClickMetric.platform_id == platform_id,
            BrandAdClickMetric.shop_no == shop_no,
            BrandAdClickMetric.metric_date == d,
        )
    )
    if existing is None:
        db.add(BrandAdClickMetric(
            store_id=store_id, platform_id=platform_id, shop_no=shop_no, metric_date=d,
            ad_spend=ad_spend, impressions=impressions, clicks=clicks,
            ad_orders=ad_orders, ad_revenue=ad_revenue,
        ))
        # upsert_daily_settlement와 같은 이유(autoflush=False인
        # app.db.SessionLocal) — 같은 브랜드의 여러 달 응답을 한 세션 안에서
        # 연달아 upsert하므로 flush 없이는 두 번째 호출부터 select()가 방금
        # add()한 행을 못 봐서 중복 INSERT를 시도한다(오늘 매출/입금
        # upsert에서 실제로 겪은 UniqueViolation과 같은 버그 클래스).
        db.flush()
        return
    existing.ad_spend = ad_spend
    existing.impressions = impressions
    existing.clicks = clicks
    existing.ad_orders = ad_orders
    existing.ad_revenue = ad_revenue
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_brand_ad_click_metric`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 5: `_run_sync`에 우리가게클릭 동기화를 통합하는 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 상단 import에 다음을 추가(기존 import 유지):

```python
from scrapers.baemin_ads import BaeminAdsScrapeError
```

`sync_setup` 픽스처(현재 `fetch_shop_stats`/`fetch_account_settlement`/`fetch_current_month_orders`에 안전한 기본값을 monkeypatch하는 부분)에 한 줄을 추가해서, 이 태스크가 추가하는 새 fetch 함수도 기존 리뷰/매출 전용 테스트들에서 AttributeError 없이 기본적으로 빈 결과를 반환하도록 만든다. `sync_setup` 함수 안의 다음 블록:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
```

를 다음으로 교체:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", lambda page, shop_no, months: [])
```

파일 끝에 추가:

```python
_CLICK_RESP_AUGUST = {
    "summary": {"displayCount": 201, "clickCount": 6, "orderCount": 0, "orderAmounts": 0,
                "clickRate": 2.985, "orderRate": 0.0, "spentBudget": 570, "returnOnAdSpend": 0.0},
    "metrics": {},
    "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 95, "displayCount": 40, "clickCount": 1,
         "orderCount": 0, "orderAmounts": 0, "returnOnAdSpend": 0.0},
    ],
}


def test_sync_upserts_brand_click_metrics_per_shop(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()  # shop_no=99999001
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_click_metrics",
        lambda page, shop_no, months: [_CLICK_RESP_AUGUST],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="99999001", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95
    assert row.clicks == 1


def test_sync_sums_nothing_across_brands_for_click_metrics(db_session, sync_setup, monkeypatch):
    """매출/재주문율과 달리 브랜드별로 완전히 분리 저장돼야 한다 — 서로 다른
    shop_no는 서로 다른 행이지 합산 대상이 아니다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    click_a = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 100, "displayCount": 10, "clickCount": 1, "orderCount": 0, "orderAmounts": 0},
    ]}
    click_b = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 200, "displayCount": 20, "clickCount": 2, "orderCount": 0, "orderAmounts": 0},
    ]}

    def _fetch_click(page, shop_no, months):
        return [click_a] if shop_no == 11111 else [click_b]

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_click)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row_a = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="11111", metric_date="2026-08-01",
    ).one()
    row_b = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="22222", metric_date="2026-08-01",
    ).one()
    assert row_a.ad_spend == 100  # 합산 안 됨, 각자 따로
    assert row_b.ad_spend == 200


def test_sync_isolates_one_brand_click_metrics_failure_from_other_brands(db_session, sync_setup, monkeypatch):
    """한 브랜드의 우리가게클릭 조회 실패(예: 캠페인이 없는 브랜드)가 다른
    브랜드의 정상 수집을 막지 않아야 한다 — 리뷰/매출과 같은 브랜드별 독립
    실패 격리 원칙."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    click_b = {"summary": {}, "metrics": {}, "dailyMetrics": [
        {"date": "2026-08-01", "spentBudget": 200, "displayCount": 20, "clickCount": 2, "orderCount": 0, "orderAmounts": 0},
    ]}

    def _fetch_click(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminAdsScrapeError("우리가게클릭 캠페인을 찾을 수 없습니다")
        return [click_b]

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_click)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert db_session.query(BrandAdClickMetric).filter_by(shop_no="11111").count() == 0
    row_b = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="22222", metric_date="2026-08-01",
    ).one()
    assert row_b.ad_spend == 200
    assert "우리가게클릭" in job.error_message
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k click_metric`
Expected: FAIL — `_run_sync`가 아직 `fetch_brand_click_metrics`를 호출하지 않으므로 `BrandAdClickMetric` 행이 생성되지 않아 `.one()`이 `NoResultFound`를 낸다.

- [ ] **Step 7: `_run_sync`에 브랜드별 우리가게클릭 동기화 추가**

`backend/app/review_sync.py`의 `_run_sync` 함수 안, 정산(입금) 동기화 블록(`except Exception as e: stats_errors.append(f"정산(입금) 동기화 실패: {e}")`로 끝나는 블록) 바로 다음, `finally: session.close()` 앞에 삽입:

```python
        # 우리가게클릭은 매출/입금/재주문율과 달리 계정 전체로 합산하지
        # 않는다 — 애초에 브랜드(shop_no) 단위로만 조회되는 화면이라
        # 브랜드별로 완전히 분리해서 저장한다(설계 문서 스코프 결정).
        # 그래서 fetch_shop_stats처럼 매장 루프 안에서 브랜드마다 upsert도
        # 그 자리에서 바로 한다 — 나중에 합치는 단계가 없다.
        for shop_no, shop_name in session.shops:
            try:
                click_responses = fetch_brand_click_metrics(session.page, shop_no, months)
                click_by_date = map_click_metrics_by_date(click_responses)
                for metric_date, m in click_by_date.items():
                    upsert_brand_ad_click_metric(
                        db, job.store_id, job.platform_id, str(shop_no), metric_date,
                        ad_spend=m["ad_spend"], impressions=m["impressions"], clicks=m["clicks"],
                        ad_orders=m["ad_orders"], ad_revenue=m["ad_revenue"],
                    )
                if click_by_date:
                    stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"{shop_name} 우리가게클릭 동기화 실패: {e}")
    finally:
        session.close()
```

(마지막 줄 `finally: session.close()`는 기존 코드와 동일하다 — 이 삽입으로 대체되는 게 아니라, 새 블록이 그 바로 앞에 추가되는 것이다. 편집 도구로 정확히 위치를 맞춘다.)

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k click_metric`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 10: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 데이터 동기화에 브랜드별 우리가게클릭 성과 upsert 통합"
```

---

### Task 5: 백엔드 엔드포인트 — `GET /ads/click-performance`

**Files:**
- Modify: `backend/app/routers/ads.py`
- Test: `backend/tests/test_ads.py`

**Interfaces:**
- Consumes: Task 1의 `BrandAdClickMetric` 모델. 기존 `app.acos.calculate_performance`(이미 있음, `/ads/performance`가 쓰는 것과 동일한 함수).
- Produces: `GET /ads/click-performance?store_id=&shop_no=&days=` — 응답 `{"shop_no": str, "period_days": int, "ad_spend": int, "impressions": int, "clicks": int, "ad_orders": int, "ad_revenue": int, "cpc": float, "cvr": float, "aov": float, "acos": float | None, "score": int | None}`. Task 6(프론트엔드)이 이 엔드포인트를 그대로 호출한다.

- [ ] **Step 1: 기존 `test_ads.py` import 확인**

`backend/tests/test_ads.py`는 이미 있다(`ads_performance`/`ads_rank_monitoring`/`ads_rank_by_distance` 테스트). 현재 상단 import는:

```python
from datetime import date, datetime, timezone

from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, Order
```

`from app.models import ...` 줄만 다음으로 교체(`date`/`datetime`/`timezone` import는 그대로 둔다 — 이미 있음, 새로 추가하지 않는다):

```python
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, BrandAdClickMetric, Order
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_ads.py` 파일 끝에 추가(아래 코드 블록의 `from datetime import date`/`from app.models import BrandAdClickMetric` 줄은 Step 1에서 이미 처리했으므로 다시 추가하지 않는다 — 실제로 추가할 내용은 세 `def test_click_performance_...` 함수뿐이다):

```python
def test_click_performance_computes_acos_from_real_formula(client, db_session, seeded_user, platforms, auth_headers):
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date=date(2026, 8, 1),
        ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
    ))
    db_session.commit()

    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=14804914&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["shop_no"] == "14804914"
    assert body["ad_spend"] == 34730
    assert body["clicks"] == 106
    # CPC = 34730 / 106 ≈ 327.64
    assert body["cpc"] == round(34730 / 106, 2)
    # CVR = 16 / 106 ≈ 0.1509
    assert body["cvr"] == round(16 / 106, 4)
    assert body["acos"] is not None
    assert body["score"] is not None


def test_click_performance_scopes_to_requested_shop_no_only(client, db_session, seeded_user, platforms, auth_headers):
    db_session.add_all([
        BrandAdClickMetric(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
            shop_no="14804912", metric_date=date(2026, 8, 1),
            ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
        ),
        BrandAdClickMetric(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
            shop_no="14804914", metric_date=date(2026, 8, 1),
            ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
        ),
    ])
    db_session.commit()

    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=14804912&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert body["ad_spend"] == 95  # 14804914분이 섞이면 안 됨


def test_click_performance_no_data_returns_zeroed_response(client, db_session, seeded_user, platforms, auth_headers):
    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=99999999&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ad_spend"] == 0
    assert body["acos"] is None  # 분모 0 — 계산 불가
    assert body["score"] is None
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k click_performance`
Expected: FAIL — `404 Not Found` (엔드포인트가 아직 없음)

- [ ] **Step 4: `backend/app/routers/ads.py`에 엔드포인트 추가**

파일 상단 import 블록의 `from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, Order, Store, User`를 다음으로 교체:

```python
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, BrandAdClickMetric, Order, Store, User
```

`ads_performance` 함수(`/ads/performance`) 바로 다음에 삽입:

```python
@router.get("/ads/click-performance")
def ads_click_performance(
    shop_no: str,
    store_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """브랜드(shop_no) 단위 우리가게클릭 실데이터 성과. 기존 `/ads/performance`
    (카테고리 기반 `ad_campaigns`, Mock)와 완전히 별개 — `ad_campaigns`를
    전혀 참조하지 않는다."""
    sid = store_id or get_user_default_store_id(user, db)
    since = date.today() - timedelta(days=days)

    agg = db.execute(
        select(
            func.coalesce(func.sum(BrandAdClickMetric.ad_spend), 0),
            func.coalesce(func.sum(BrandAdClickMetric.impressions), 0),
            func.coalesce(func.sum(BrandAdClickMetric.clicks), 0),
            func.coalesce(func.sum(BrandAdClickMetric.ad_orders), 0),
            func.coalesce(func.sum(BrandAdClickMetric.ad_revenue), 0),
        ).where(
            BrandAdClickMetric.store_id == sid,
            BrandAdClickMetric.shop_no == shop_no,
            BrandAdClickMetric.metric_date >= since,
        )
    ).one()
    ad_spend, impressions, clicks, ad_orders, ad_revenue = agg
    perf = calculate_performance(ad_spend, clicks, ad_orders, ad_revenue)

    return {
        "shop_no": shop_no,
        "period_days": days,
        "ad_spend": perf.ad_spend,
        "impressions": impressions,
        "clicks": perf.clicks,
        "ad_orders": perf.ad_orders,
        "ad_revenue": perf.ad_revenue,
        "cpc": perf.cpc,
        "cvr": perf.cvr,
        "aov": perf.aov,
        "acos": perf.acos,
        "score": perf.score,
    }
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k click_performance`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/ads.py backend/tests/test_ads.py
git commit -m "feat: 브랜드별 우리가게클릭 실성과 조회 엔드포인트 GET /ads/click-performance 추가"
```

---

### Task 6: 프론트엔드 — 대시보드 "우가클 점수" 카드에 브랜드 선택 드롭다운

**Files:**
- Modify: `frontend/src/app/(app)/dashboard/page.tsx`

**Interfaces:**
- Consumes: 기존 `GET /store-connections/baemin/shops?store_id=`(리뷰 관리 화면이 이미 쓰는 것과 동일한 엔드포인트, 새로 안 만듦). Task 5의 `GET /ads/click-performance?store_id=&shop_no=&days=`.
- Produces: 없음(이 태스크가 계획의 마지막 화면 변경).

- [ ] **Step 1: `UgacleModal`을 브랜드별 실데이터로 교체**

`frontend/src/app/(app)/dashboard/page.tsx`의 `type AdPerformance` 타입 선언을 다음으로 교체:

```typescript
type AdPerformance = {
  category: string; cpc: number; cvr: number; aov: number; acos: number | null; score: number | null;
  order_share: number | null; clicks: number; ad_orders: number;
};
type ClickPerformance = {
  shop_no: string; period_days: number; ad_spend: number; impressions: number; clicks: number;
  ad_orders: number; ad_revenue: number; cpc: number; cvr: number; aov: number;
  acos: number | null; score: number | null;
};
type ShopBrand = { shop_no: string; shop_name: string };
```

`UgacleModal` 함수 전체를 다음으로 교체:

```typescript
function UgacleModal({ storeId, shopNo }: { storeId: number; shopNo: string }) {
  const [perf, setPerf] = useState<ClickPerformance | null>(null);
  useEffect(() => {
    if (!shopNo) return;
    apiGet<ClickPerformance>(`/ads/click-performance?store_id=${storeId}&shop_no=${shopNo}&days=14`).then(setPerf);
  }, [storeId, shopNo]);

  if (perf === null) return <p className="text-sm text-muted">불러오는 중...</p>;
  return (
    <div>
      <p className="mb-4 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        최근 14일 우리가게클릭(배민 실데이터) 성과를 집계한 값입니다.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">주문전환율 (CVR)</p>
          <p className="mt-1 text-lg font-bold">{(perf.cvr * 100).toFixed(1)}%</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">주문당 광고비율 (ACoS)</p>
          <p className="mt-1 text-lg font-bold">{perf.acos !== null ? `${perf.acos}%` : "—"}</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">노출수</p>
          <p className="mt-1 text-lg font-bold">{perf.impressions.toLocaleString()}회</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">클릭당 단가 (CPC)</p>
          <p className="mt-1 text-lg font-bold">{won(Math.round(perf.cpc))}</p>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between rounded-lg border border-accent/30 bg-accent-soft p-3">
        <span className="text-xs text-muted">종합 성과 점수</span>
        <span className="text-xl font-bold text-accent">{perf.score ?? "—"}점</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `DashboardPage`에 브랜드 목록 조회 + 선택 상태 추가**

`DashboardPage` 함수 안, `const [openModal, setOpenModal] = useState<...>` 다음 줄에 삽입:

```typescript
  const [brands, setBrands] = useState<ShopBrand[]>([]);
  const [selectedShopNo, setSelectedShopNo] = useState("");
  const [clickPerf, setClickPerf] = useState<ClickPerformance | null>(null);
```

`useEffect(() => { if (!storeId) return; apiGet<DashboardResponse>(...)` 블록 다음에 새 `useEffect`를 추가:

```typescript
  useEffect(() => {
    if (!storeId) return;
    apiGet<ShopBrand[]>(`/store-connections/baemin/shops?store_id=${storeId}`).then((b) => {
      setBrands(b);
      if (b.length > 0) setSelectedShopNo((prev) => prev || b[0].shop_no);
    });
  }, [storeId]);

  useEffect(() => {
    if (!storeId || !selectedShopNo) return;
    apiGet<ClickPerformance>(`/ads/click-performance?store_id=${storeId}&shop_no=${selectedShopNo}&days=14`).then(setClickPerf);
  }, [storeId, selectedShopNo]);
```

- [ ] **Step 3: "우가클 점수" 카드에 브랜드 드롭다운 추가**

`<ClickableCard title="우가클 점수" onClick={() => setOpenModal("ugacle")}>` 블록 전체를 다음으로 교체:

```typescript
        <div className="rounded-2xl border border-border-subtle bg-surface p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">우가클 점수</h2>
            {brands.length > 0 && (
              <select
                value={selectedShopNo}
                onChange={(e) => setSelectedShopNo(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="rounded-lg border border-border-subtle bg-surface-2 px-2 py-1 text-xs outline-none focus:border-accent"
              >
                {brands.map((b) => (
                  <option key={b.shop_no} value={b.shop_no}>
                    {b.shop_name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <button onClick={() => setOpenModal("ugacle")} className="w-full text-left">
            <p className="text-2xl font-bold text-accent">{clickPerf?.score ?? "—"}점</p>
            <p className="mt-1 text-xs text-muted">ACoS {clickPerf?.acos ?? "—"}%</p>
          </button>
        </div>
```

- [ ] **Step 4: 모달 호출부에 `shopNo` 전달**

```typescript
      {openModal === "ugacle" && (
        <Modal title="우가클 점수" onClose={() => setOpenModal(null)}><UgacleModal storeId={storeId} shopNo={selectedShopNo} /></Modal>
      )}
```

- [ ] **Step 5: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 6: 로컬에서 실제 계정으로 동작 확인**

로컬에서 실행 중인 백엔드/프론트엔드(둘 다 없으면 각각 새로 띄운다)로 브라우저에서 대시보드에 접속해, "우가클 점수" 카드의 드롭다운으로 브랜드를 바꿀 때마다 점수/ACoS가 그 브랜드의 실제 값으로 바뀌는지 확인한다. "데이터 동기화" 버튼을 눌러 최신 데이터를 먼저 받아온 뒤 확인한다.

- [ ] **Step 7: 커밋**

```bash
git add "frontend/src/app/(app)/dashboard/page.tsx"
git commit -m "feat: 대시보드 우가클 점수 카드에 브랜드 선택 드롭다운과 실데이터 연동"
```

---

### Task 7: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 없음.
- Produces: 없음(문서 전용 태스크).

- [ ] **Step 1: "배민 매출·입금·재주문율 연동 (예외 허용)" 절 바로 뒤에 새 절 추가**

`CLAUDE.md`에서 "### 배민 매출·입금·재주문율 연동 (예외 허용)" 절의 끝(다음 `###` 헤더 直前, 현재는 "### 모바일 앱 (예외 허용)" 절 바로 앞)에 아래 절을 삽입한다:

```markdown
### 배민 우리가게클릭(우가클) 브랜드별 실데이터 연동 (예외 허용)
원래 "우가클 점수"는 카테고리 기반 `ad_campaigns`/`ad_performance_metrics`의
Mock 데이터로만 계산됐으나, 실 SaaS 전환 로드맵 3번의 다음 단계로 브랜드별
실데이터를 연동하기로 결정했다(2026-08-12). 사장님광장의 "광고·서비스관리
→ 우리가게클릭 → 마케팅 성과" 화면(`GET
/v2/statistics/campaign/cpc/metrics/{shopNumber}`)의 organic 응답을 리뷰·
매출과 동일한 방식으로 브랜드(shop_no)별로 가로챈다. 이 화면은 매출/입금과
달리 브랜드 단위로만 조회되고 계정 전체 통합 화면이 없어서, 계정 전체
합산이 아니라 **브랜드별로 완전히 분리해서** 저장하기로 결정했다 — 새 테이블
`brand_ad_click_metrics`를 추가했다(기존 `ad_campaigns`/`ad_performance_metrics`/
`ad_rank_snapshots`, 카테고리 기반 "광고 순위 모니터링"은 전혀 건드리지
않고 완전히 별개로 남아있다). 계산값(CPC/CVR/AOV/ACoS/점수)은 저장하지
않고 `acos.py`가 조회 시 실제 공식으로 계산하는 기존 정규화 원칙을
그대로 따른다. 백필은 이번 달 포함 최근 3개월(매출/입금과 동일한 폭),
"우가클 주문 비중"(전체 주문 대비 광고 경유 비중)은 분모가 브랜드별로 안
나뉘어 왜곡되므로 범위 밖으로 뺐다. "가게 연결" 화면의 "데이터 동기화"
버튼이 리뷰·매출·입금·재주문율에 이어 브랜드별 우가클 성과까지 한 번에
가져온다. "절대 금지"의 "실제 CPC 자동 입찰 금지" 원칙은 그대로 유효—
이번은 성과 조회(읽기)만이고 캠페인 설정을 바꾸는 기능은 아니다. 설계
상세는
`docs/superpowers/specs/2026-08-12-baemin-brand-ad-click-performance-design.md`
참고.
```

- [ ] **Step 2: DB 설계 절의 테이블 목록·개수 갱신**

`CLAUDE.md`의 "## DB 설계 (19개 테이블)" 헤더와 그 아래 테이블 나열 목록을 찾는다(Task 1~5에서 이미 21개로 늘었을 스키마와 맞춰야 한다 — `schema.sql`의 최종 테이블 개수를 `grep -c "^CREATE TABLE" schema.sql`로 확인 후 그 숫자로 헤더를 바꾸고, 나열 목록 끝에 `brand_ad_click_metrics`를 추가한다). "### 테이블 용도" 절에도 한 줄 추가:

```markdown
- brand_ad_click_metrics: 브랜드(shop_no)별 일별 우리가게클릭 광고 성과
  원본(노출/클릭/주문/광고비/광고매출). 계정 전체 합산이 아니라 브랜드별로
  완전히 분리 저장한다(우리가게클릭 화면 자체가 브랜드 단위로만 조회되기
  때문). ad_campaigns(카테고리 기반, 광고 순위 모니터링용)와는 별개.
```

"### 핵심 관계" 절에도 한 줄 추가:

```markdown
- brand_ad_click_metrics는 store와 platform 참조 (shop_no는 FK가 아니라 값만 저장)
```

- [ ] **Step 3: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: 배민 우리가게클릭 브랜드별 실데이터 연동 CLAUDE.md 반영"
```

---

## Self-Review 결과 (계획 작성자 자체 점검)

- **스펙 커버리지**: 설계 문서의 데이터 모델(Task 1) · 순수 매핑 함수(Task 2) · fetch 함수(Task 3) · 동기화 통합(Task 4) · 새 엔드포인트(Task 5) · 대시보드 UI(Task 6) · CLAUDE.md(Task 7) 전부 태스크로 매핑됨. 설계 문서의 "범위 밖" 항목(쿠팡이츠/요기요, order_share, 12개월 이상 소급, 캠페인 설정 변경)은 어떤 태스크에도 포함되지 않음 — 의도대로.
- **타입 일관성**: `shop_no`는 전 구간에서 `str`로 통일(`fetch_brand_click_metrics`의 `shop_no: int` 파라미터만 예외 — `session.shops`가 주는 원본 타입 그대로 받고, `upsert_brand_ad_click_metric` 호출 시 `str(shop_no)`로 변환. `baemin_shop_brands.shop_no`/`brand_ad_click_metrics.shop_no` 둘 다 `VARCHAR(20)`으로 일치).
- **플레이스홀더 스캔**: `NotImplementedError`나 "TBD" 없음 — Task 3의 fetch 함수는 이미 실 계정으로 라이브 검증된 구체적 코드로 작성됨(리뷰/매출 스크래핑 때와 달리 계획 수립 단계에서 조사를 이미 마쳤기 때문).
