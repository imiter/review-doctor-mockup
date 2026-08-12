# 배민 정산 상세(수수료·배달비·고객할인·우가클비용) 실데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "매출 분석" 카드(`SalesBreakdownModal`)의 배민 행이 요율 기반 추정치(중개수수료/결제수수료) 대신, 배민 정산내역의 실제 차감 내역(수수료/배달비/고객할인/우가클 광고비)을 보여주도록 만든다.

**Architecture:** 정산내역 화면(`/orders/billing`)의 배치 카드를 클릭하면 나오는 `GET /v3/settle/history/details/{giveId}` organic 응답을 캡처해(기존 리뷰/매출/입금 스크래핑과 동일한 `page.on("response")` 패턴), 순수 함수로 날짜별 4개 카테고리(수수료/배달비/고객할인/우가클비용)로 집계한 뒤 `daily_settlements`에 새 컬럼 4개로 upsert한다. "기타" 항목은 저장하지 않고 `/sales/breakdown` 조회 시점에 잔차로 계산한다. 새 엔드포인트나 새 동기화 버튼은 만들지 않는다 — 기존 `/sales/breakdown`과 기존 "데이터 동기화" 흐름을 확장한다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API), Next.js App Router.

## Global Constraints

- 배민만. 요기요/쿠팡이츠는 계속 추정치(변경 없음).
- 새 컬럼 4개(`commission_amount`, `delivery_fee_amount`, `customer_discount_amount`, `ad_cost_amount`)는 전부 nullable, 전부 양수로 저장한다("차감된 금액").
- "기타"(misc)는 컬럼을 만들지 않는다 — `/sales/breakdown` 조회 시점에 `sales_amount − commission_amount − delivery_fee_amount − customer_discount_amount − ad_cost_amount − deposit_amount`로 계산한다(정규화 원칙: 요약을 물리 테이블로 중복 저장하지 않는다).
- 정산배치(giveId)는 여러 날짜에 걸쳐도 `depositDueDate` 하루에 귀속한다 — 기존 `deposit_amount`(`map_deposits_by_date`)와 동일한 패턴. 우가클비용의 배치 내 일자별 세부(`cpcDetails.dailyDetails`)는 이번 범위에서 안 쓴다.
- 정산 **summary**(`fetch_account_settlement`, 입금액 `deposit_amount`용)는 기존 그대로 90일 창을 유지한다 — 이번 작업으로 건드리지 않는다. 정산 **상세**(신규)는 별도로 최근 30일 창만 수집한다 — 배치 하나당 카드 클릭 1번이 필요해서 90일 전부를 상세까지 클릭하면 동기화 시간이 크게 늘어난다는 트레이드오프를 사용자와 확인해 범위를 좁혔다(2026-08-12).
- `fetch_settlement_breakdown_details`(Playwright 화면 조작)는 자동화된 pytest로 덮지 않는다 — 이 저장소 컨벤션(`fetch_account_settlement`/`fetch_brand_click_metrics`와 동일)이다. 순수 매핑 함수와 `_run_sync` 오케스트레이션 로직만 pytest로 촘촘히 테스트한다.
- 새 엔드포인트를 만들지 않는다 — 기존 `GET /sales/breakdown`을 확장하고, 기존 "데이터 동기화" 버튼/작업(`review_sync.py`의 `_run_sync`)에 통합한다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 DB 정본이라 `ALTER TABLE`이 아니라 `CREATE TABLE` 문 자체를 수정한다.
- 참고 스펙: `docs/superpowers/specs/2026-08-12-baemin-settlement-fee-breakdown-design.md`

---

### Task 1: 데이터 모델 — `daily_settlements`에 컬럼 4개 추가

**Files:**
- Modify: `schema.sql` (`daily_settlements` CREATE TABLE 블록)
- Modify: `backend/app/models.py` (`DailySettlement` 모델)
- Test: `backend/tests/test_sales.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: 없음.
- Produces: `DailySettlement` 모델에 nullable 필드 4개(`commission_amount`, `delivery_fee_amount`, `customer_discount_amount`, `ad_cost_amount`, 전부 `int | None`). 이후 모든 태스크가 이 필드들을 그대로 쓴다.

- [ ] **Step 1: `schema.sql` — `daily_settlements` 블록에 컬럼 4개 추가**

`schema.sql`에서 다음 블록을 찾는다:

```sql
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
```

다음으로 교체:

```sql
-- 11. daily_settlements — 일별 매출과 입금을 함께 저장 (정산 지연 반영)
--     매출 요약 테이블은 따로 두지 않는다. 기간 요약은 이 테이블을 집계한다.
--     commission_amount~ad_cost_amount는 배민 정산 상세(실측)로 채워지는
--     nullable 컬럼이다 — 요기요/쿠팡이츠 행과, 아직 정산 상세 동기화가
--     안 된 배민 과거 날짜는 NULL로 남는다("데이터 없음"과 "차감액 0원"을
--     구분하기 위해 NOT NULL DEFAULT 0을 쓰지 않는다). "기타" 항목은
--     컬럼을 두지 않고 조회 시점에 잔차로 계산한다(정규화 원칙).
-- ----------------------------------------------------------------------------
CREATE TABLE daily_settlements (
    id                        BIGSERIAL PRIMARY KEY,
    store_id                  BIGINT NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id               INT    NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    settle_date               DATE   NOT NULL,
    sales_amount              INT    NOT NULL DEFAULT 0 CHECK (sales_amount >= 0),   -- 그날 발생한 매출액 (원)
    deposit_amount            INT    NOT NULL DEFAULT 0 CHECK (deposit_amount >= 0), -- 그날 실제 입금된 금액 (원)
    commission_amount         INT    CHECK (commission_amount >= 0),         -- 중개수수료+결제수수료 (배민 실측, 양수)
    delivery_fee_amount       INT    CHECK (delivery_fee_amount >= 0),       -- 배달비 (배민 실측, 양수)
    customer_discount_amount  INT    CHECK (customer_discount_amount >= 0),  -- 고객 즉시할인 (배민 실측, 양수)
    ad_cost_amount            INT    CHECK (ad_cost_amount >= 0),            -- 우가클(CPC) 광고비 (배민 실측, 양수)
    UNIQUE (store_id, platform_id, settle_date)
);
```

(Postgres에서 `CHECK (col >= 0)`은 `col`이 NULL이면 결과가 UNKNOWN이라 제약을 통과한다 — `sales_amount`/`deposit_amount`와 똑같은 문법을 그대로 쓰되 `NOT NULL DEFAULT 0`만 빠졌다.)

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_sales.py` 파일 끝에 추가:

```python
def test_daily_settlement_breakdown_columns_default_to_null(db_session, seeded_user, platforms):
    store, platform = seeded_user["store"], platforms["baemin"]
    row = DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=1_000, deposit_amount=900,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    assert row.commission_amount is None
    assert row.delivery_fee_amount is None
    assert row.customer_discount_amount is None
    assert row.ad_cost_amount is None


def test_daily_settlement_breakdown_columns_store_explicit_values(db_session, seeded_user, platforms):
    store, platform = seeded_user["store"], platforms["baemin"]
    row = DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=1_000, deposit_amount=900,
        commission_amount=100, delivery_fee_amount=50,
        customer_discount_amount=30, ad_cost_amount=20,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    assert row.commission_amount == 100
    assert row.delivery_fee_amount == 50
    assert row.customer_discount_amount == 30
    assert row.ad_cost_amount == 20
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_sales.py -v -k breakdown_columns`
Expected: FAIL — `TypeError: 'commission_amount' is an invalid keyword argument for DailySettlement`

- [ ] **Step 4: `backend/app/models.py` — `DailySettlement`에 필드 4개 추가**

`class DailySettlement` 블록(`deposit_amount: Mapped[int] = mapped_column(default=0)`로 끝나는 줄) 다음, `platform: Mapped[Platform] = relationship()` 줄 앞에 삽입:

```python
    commission_amount: Mapped[int | None] = mapped_column(default=None)
    delivery_fee_amount: Mapped[int | None] = mapped_column(default=None)
    customer_discount_amount: Mapped[int | None] = mapped_column(default=None)
    ad_cost_amount: Mapped[int | None] = mapped_column(default=None)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_sales.py -v -k breakdown_columns`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 7: 로컬 검증 DB에 스키마 재적용**

이 프로젝트는 Alembic이 없어 스키마 변경은 DB를 다시 만들어 반영한다. 로컬 검증용 Docker Postgres(`baemin-verify-db2`, 이미 이 세션에서 쓰던 컨테이너)가 있다면 `schema.sql`을 다시 적용해 새 컬럼이 실제로 생기는지 확인한다:

```bash
psql postgresql://postgres:postgres@localhost:15432/delivery_insight -c "\d daily_settlements" | grep -E "commission_amount|delivery_fee_amount|customer_discount_amount|ad_cost_amount"
```

새로 스키마를 적용해야 한다면(기존 실데이터를 보존해야 하는 로컬 검증 환경이라면) `ALTER TABLE daily_settlements ADD COLUMN ...`을 로컬에서만 임시로 실행해 컬럼을 추가하는 것도 가능하다 — `schema.sql` 자체는 계속 `CREATE TABLE`만 정본으로 유지한다(이 저장소는 마이그레이션 파일을 만들지 않는다).

- [ ] **Step 8: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_sales.py
git commit -m "feat: daily_settlements에 정산 상세(수수료/배달비/고객할인/우가클비용) 컬럼 추가"
```

---

### Task 2: 순수 매핑 함수 — 정산 상세 API 응답 → 날짜별 4개 카테고리

**Files:**
- Modify: `backend/scrapers/baemin_stats.py`
- Test: `backend/tests/test_baemin_stats.py`

**Interfaces:**
- Consumes: 없음(외부 의존 없는 순수 함수).
- Produces: `map_settlement_breakdown_by_date(details: list[dict]) -> dict[str, dict]`(키: `commission_amount`, `delivery_fee_amount`, `customer_discount_amount`, `ad_cost_amount`, 전부 양수 int). Task 4가 이 함수를 그대로 가져다 쓴다. 입력 `details`는 각 항목이 `{"giveId": int, "depositDueDate": str | None, **원본 상세 JSON}` 형태다(Task 3의 `fetch_settlement_breakdown_details`가 반환하는 정확한 형태 — Task 3보다 먼저 구현하지만 그 반환 형태를 미리 약속해둔다).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_baemin_stats.py` 파일 끝에 추가:

```python
from scrapers.baemin_stats import map_settlement_breakdown_by_date

# 실 계정(2026-08-12 조사)에서 직접 캡처한 GET
# /v3/settle/history/details/{giveId} 응답(giveId=531969790, 정산기간
# 26.08.07~26.08.09, 입금일 26-08-12)을 필요한 필드만 남겨 축약한 것.
# 검산: baemin1Details.giveAmount + baeminDetails.giveAmount +
# etcDetails.total + cpcDetails.total == 최상위 giveAmount (904812) —
# 실 계정으로 직접 확인 완료.
_SETTLE_DETAIL_531969790 = {
    "giveAmount": 904812,
    "baemin1Details": {
        "giveAmount": 936472,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -102741},
            "benefitsAmount": {"total": -266760},
        },
        "delivery": {"deliverySupplyPrice": {"total": -210800}},
        "extra": {"paymentFee": {"total": -27329}},
    },
    "baeminDetails": {
        "giveAmount": 16435,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -1081},
            "benefitsAmount": {"total": -5000},
        },
        "delivery": {"deliverySupplyPrice": {"total": 0}},
        "extra": {"paymentFee": {"total": -251}},
    },
    "etcDetails": {"total": 0},
    "cpcDetails": {"total": -48095},
}
_TAGGED_DETAIL = {"giveId": 531969790, "depositDueDate": "2026-08-12", **_SETTLE_DETAIL_531969790}


def test_map_settlement_breakdown_by_date_computes_four_categories():
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL])
    assert result == {
        "2026-08-12": {
            "commission_amount": 131_402,        # (102741+27329) + (1081+251)
            "delivery_fee_amount": 210_800,       # 210800 + 0
            "customer_discount_amount": 271_760,  # 266760 + 5000
            "ad_cost_amount": 48_095,             # -(-48095)
        }
    }


def test_map_settlement_breakdown_by_date_reconciles_with_top_level_give_amount():
    """검산: baemin1Details.giveAmount + baeminDetails.giveAmount +
    etcDetails.total + cpcDetails.total == 최상위 giveAmount. 이 fixture
    자체가 실 계정으로 검산된 값이라는 걸 보장하는 회귀 테스트."""
    d = _SETTLE_DETAIL_531969790
    total = (
        d["baemin1Details"]["giveAmount"] + d["baeminDetails"]["giveAmount"]
        + d["etcDetails"]["total"] + d["cpcDetails"]["total"]
    )
    assert total == d["giveAmount"] == 904_812


def test_map_settlement_breakdown_by_date_sums_multiple_batches_same_date():
    other = {
        "giveId": 111_111_111, "depositDueDate": "2026-08-12",
        "giveAmount": 10_000,
        "baemin1Details": {
            "giveAmount": 10_000,
            "orderBrokerage": {
                "serviceFeeAmount": {"total": -1_000},
                "benefitsAmount": {"total": -500},
            },
            "delivery": {"deliverySupplyPrice": {"total": -2_000}},
            "extra": {"paymentFee": {"total": -300}},
        },
        "baeminDetails": None,
        "etcDetails": {"total": 0},
        "cpcDetails": {"total": 0},
    }
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL, other])
    assert result["2026-08-12"]["commission_amount"] == 131_402 + 1_300   # 1000+300
    assert result["2026-08-12"]["delivery_fee_amount"] == 210_800 + 2_000
    assert result["2026-08-12"]["customer_discount_amount"] == 271_760 + 500
    assert result["2026-08-12"]["ad_cost_amount"] == 48_095


def test_map_settlement_breakdown_by_date_dedupes_same_give_id():
    result = map_settlement_breakdown_by_date([_TAGGED_DETAIL, _TAGGED_DETAIL])
    assert result["2026-08-12"]["ad_cost_amount"] == 48_095  # 두 번 더해지면 안 됨


def test_map_settlement_breakdown_by_date_skips_entries_without_deposit_due_date():
    orphan = {**_SETTLE_DETAIL_531969790, "giveId": 999, "depositDueDate": None}
    assert map_settlement_breakdown_by_date([orphan]) == {}


def test_map_settlement_breakdown_by_date_empty_list_returns_empty_dict():
    assert map_settlement_breakdown_by_date([]) == {}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v -k settlement_breakdown`
Expected: FAIL — `ImportError: cannot import name 'map_settlement_breakdown_by_date' from 'scrapers.baemin_stats'`

- [ ] **Step 3: `backend/scrapers/baemin_stats.py`에 `map_settlement_breakdown_by_date` 추가**

`map_deposits_by_date` 함수 다음, `map_repurchase_by_date` 함수 앞에 삽입:

```python
def _settlement_breakdown_amounts(detail: dict) -> dict[str, int]:
    """정산 상세 응답 하나(`giveId` 하나)에서 4개 카테고리를 양수로 계산한다
    (설계 문서 "API 응답 매핑" 절 공식). `baemin1Details`(한집배달·알뜰배달)와
    `baeminDetails`(가게배달·바로결제) 두 블록을 합산하고, 우가클비용만
    최상위 `cpcDetails`에서 가져온다. 두 블록 중 하나가 없을 수도 있어
    (실측하지 않은 케이스지만 방어적으로) `.get()`으로 안전하게 접근한다."""
    commission = 0
    delivery = 0
    discount = 0
    for block_key in ("baemin1Details", "baeminDetails"):
        block = detail.get(block_key)
        if not block:
            continue
        commission += -block["orderBrokerage"]["serviceFeeAmount"]["total"]
        commission += -block["extra"]["paymentFee"]["total"]
        delivery += -block["delivery"]["deliverySupplyPrice"]["total"]
        discount += -block["orderBrokerage"]["benefitsAmount"]["total"]
    ad_cost = -detail["cpcDetails"]["total"]
    return {
        "commission_amount": commission,
        "delivery_fee_amount": delivery,
        "customer_discount_amount": discount,
        "ad_cost_amount": ad_cost,
    }


def map_settlement_breakdown_by_date(details: list[dict]) -> dict[str, dict]:
    """`fetch_settlement_breakdown_details`가 반환한, `depositDueDate`가
    태그된 정산 상세 리스트를 날짜별로 합산한다. 같은 `giveId`가
    페이지네이션 경계에서 중복 캡처될 수 있어(정산 요약/입금과 동일한
    현상, `map_deposits_by_date` 참고) 먼저 `giveId` 기준으로 dedupe한다.
    `depositDueDate`를 못 찾은 항목(정상 흐름에서는 발생하지 않아야 하지만
    방어적으로)은 건너뛴다."""
    by_id: dict[int, dict] = {}
    for detail in details:
        by_id[detail["giveId"]] = detail

    totals: dict[str, dict] = {}
    for detail in by_id.values():
        d = detail.get("depositDueDate")
        if d is None:
            continue
        amounts = _settlement_breakdown_amounts(detail)
        bucket = totals.setdefault(d, {
            "commission_amount": 0, "delivery_fee_amount": 0,
            "customer_discount_amount": 0, "ad_cost_amount": 0,
        })
        for k, v in amounts.items():
            bucket[k] += v
    return totals
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v -k settlement_breakdown`
Expected: 6개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/scrapers/baemin_stats.py backend/tests/test_baemin_stats.py
git commit -m "feat: 정산 상세 API 응답을 날짜별 수수료/배달비/고객할인/우가클비용으로 집계하는 순수 함수 추가"
```

---

### Task 3: 정산 상세 organic 응답 캡처 — `fetch_settlement_breakdown_details`

**Files:**
- Modify: `backend/scrapers/baemin_stats.py` (Task 2에서 만든 파일에 fetch 함수 추가)

**Interfaces:**
- Consumes: `baemin_auth.login()`이 반환한 `BaeminSession.page`(이미 인증된 살아있는 Playwright 페이지)를 그대로 받는다 — 재로그인하지 않는다. 기존 `_dismiss_backdrop_if_present`, `_open_date_range_picker`, `_set_date_range`(전부 이미 있음, 정산내역/주문내역과 공유하는 날짜 범위 다이얼로그)를 그대로 재사용한다.
- Produces: `fetch_settlement_breakdown_details(page, start_date: str, end_date: str) -> list[dict]`(반환: Task 2의 `map_settlement_breakdown_by_date`에 그대로 넘길 수 있는 태그된 상세 리스트). Task 4가 이 함수를 그대로 가져다 쓴다.

이 태스크는 조사 과정에서 카드 "하나"를 클릭해 실제 상세 API 응답을 끌어내는 방식은 실 계정으로 검증했다(2026-08-12, giveId 531969790로 재현 — 설계 문서 "조사 과정에서 확인된 사실" 절 참고). 다만 한 페이지 안에서 여러 카드를 연달아 클릭하는 것과 페이지네이션을 엮는 부분은 검증하지 않았다 — Step 2에서 실 계정으로 반드시 확인한다.

- [ ] **Step 1: `backend/scrapers/baemin_stats.py`에 카드 클릭 헬퍼와 fetch 함수 추가**

파일 상단 상수 블록(`_MONTH_CAPTION_RE = re.compile(...)` 다음 줄)에 추가:

```python
_CARD_DATE_RE = re.compile(r"^\d{1,2}월 \d{1,2}일$")
```

파일 끝(`fetch_current_month_orders` 함수 다음)에 추가:

```python


def _click_all_settlement_cards_on_page(page) -> None:
    """현재 페이지에 보이는 정산 배치 카드 전부를 순서대로 클릭해 각각의
    상세(`settle/history/details/{giveId}`) 응답을 끌어낸다. 카드는 "N월
    N일" 날짜 헤딩과 "입금완료"/"입금예정" 상태 배지를 모두 포함하는
    가장 안쪽 div로 특정한다(`has_text` 필터는 조상 div까지 전부 매칭시키므로
    `.last`로 가장 안쪽=카드 컨테이너를 고른다) — 이 방식(카드 컨테이너
    bounding box의 오른쪽 끝 클릭)이 실제 상세 API 호출을 끌어내는 걸
    실 계정으로 확인했다(2026-08-12, giveId 531969790로 재현).

    같은 날짜에 배치가 2건 이상이면 날짜 헤딩 텍스트가 중복돼 이 방식으로
    정확히 구분되지 않을 수 있다 — 이번 조사에서는 확인하지 못한 케이스라
    Step 2에서 실 계정으로 반드시 확인한다."""
    date_headings = page.get_by_text(_CARD_DATE_RE, exact=True).all_inner_texts()
    for heading_text in date_headings:
        card = page.locator("div", has_text=heading_text).filter(
            has_text=re.compile(r"^입금(완료|예정)$")
        ).last
        box = card.bounding_box()
        if box is None:
            continue
        page.mouse.click(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
        page.wait_for_timeout(1_200)


def fetch_settlement_breakdown_details(page, start_date: str, end_date: str) -> list[dict]:
    """정산내역 화면(`/orders/billing`)에서 `start_date`~`end_date` 범위의
    각 정산 배치 카드를 클릭해 상세(`settle/history/details/{giveId}`)
    응답을 모은다. `fetch_account_settlement`(입금액용 summary, 90일 창)와는
    완전히 별도의 호출이다 — 상세 수집은 카드 클릭 비용이 커서 더 좁은
    창(설계 문서 "동기화 흐름" 절, 30일)만 쓰기로 결정했기 때문에 날짜
    범위가 다르다. 같은 화면을 다시 열어 summary 응답을 한 번 더 받는 약간의
    중복 호출이 있지만(그 응답의 `contents`로 giveId → depositDueDate
    매핑만 만드는 용도), 이미 안정적으로 동작하는 `fetch_account_settlement`를
    건드리지 않고 완전히 독립적으로 두는 게 더 안전하다.

    각 상세 응답은 URL 자체에서 파싱한 `giveId`와, 같은 세션에서 받은
    summary 응답의 `contents[].{giveId, depositDueDate}`로 만든 매핑에서
    찾은 `depositDueDate`를 붙여 반환한다 — 카드의 DOM 순서가 summary
    JSON의 `contents` 순서와 반드시 일치한다고 가정하지 않아도 되는 방식이다
    (URL에 giveId가 그대로 노출되는 걸 이용). summary에서 못 찾은
    giveId(정상 흐름에서는 발생하지 않아야 함)는 `depositDueDate: None`으로
    반환하고, `map_settlement_breakdown_by_date`가 그런 항목을 건너뛴다."""
    give_id_to_date: dict[int, str] = {}
    details: list[dict] = []
    state = {"collecting": False, "observed_any": False}

    def _on_response(response) -> None:
        url = response.url
        if "self-api.baemin.com" not in url or not state["collecting"]:
            return
        path = urlparse(url).path
        if path == "/v3/settle/history/summary":
            state["observed_any"] = True
            if response.status == 200:
                try:
                    body = response.json()
                except Exception:
                    return
                for batch in body["contents"]:
                    give_id_to_date[batch["giveId"]] = batch["depositDueDate"]
        elif path.startswith("/v3/settle/history/details/"):
            if response.status == 200:
                try:
                    body = response.json()
                except Exception:
                    return
                give_id = int(path.rsplit("/", 1)[-1])
                details.append({
                    "giveId": give_id,
                    "depositDueDate": give_id_to_date.get(give_id),
                    **body,
                })

    page.on("response", _on_response)
    try:
        try:
            page.goto("https://self.baemin.com/orders/billing")
        except Exception as e:
            raise BaeminStatsScrapeError(f"정산 상세 조회를 위한 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(2_000)
        _dismiss_backdrop_if_present(page)

        state["collecting"] = True
        try:
            _open_date_range_picker(page)
            _set_date_range(page, start_date, end_date)
        except PlaywrightTimeoutError as e:
            raise BaeminStatsScrapeError(f"정산 상세 조회 날짜 범위 지정에 실패했습니다: {e}") from e
        page.wait_for_timeout(2_000)

        for _ in range(_MAX_LOAD_MORE_CLICKS):
            _click_all_settlement_cards_on_page(page)
            next_button = page.get_by_role("button", name="다음")
            if next_button.count() == 0:
                break
            try:
                next_button.first.scroll_into_view_if_needed()
                next_button.first.click(timeout=5_000)
            except PlaywrightTimeoutError:
                # 마지막 페이지에서는 "다음" 버튼이 비활성화돼 클릭이
                # 타임아웃 난다(_click_next_page_until_done과 동일한 실측
                # 확인된 종료 신호) — 정상 종료로 취급한다.
                break
            page.wait_for_timeout(_LOAD_MORE_WAIT_MS)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminStatsScrapeError("정산 상세 API 응답을 한 번도 확인하지 못했습니다")

    return details
```

- [ ] **Step 2: 실 계정으로 동작 재검증 (필수)**

이 태스크의 Global Constraints에 따라 화면 상호작용 자체는 자동화된 pytest로 덮지 않는다 — 카드 "하나" 클릭은 조사 과정에서 검증했지만, 여러 카드 연속 클릭 + 페이지네이션은 이번이 처음이라 실 계정으로 반드시 확인한다:

```bash
cd backend
.venv/bin/python -c "
import os
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:15432/delivery_insight')
os.environ.setdefault('CREDENTIAL_ENCRYPTION_KEY', '<로컬 검증용 .env의 CREDENTIAL_ENCRYPTION_KEY 값>')
from datetime import date, timedelta
from sqlalchemy import create_engine, text
from app.credential_crypto import decrypt_credential
from scrapers.baemin_auth import login
from scrapers.baemin_stats import fetch_settlement_breakdown_details, map_settlement_breakdown_by_date

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    ciphertext = conn.execute(text(\"select credential_ciphertext from store_platform_connections where platform_id = (select id from platforms where code='baemin') limit 1\")).one()[0]
cred = decrypt_credential(ciphertext)

session = login(cred['login_id'], cred['password'])
today = date.today()
details = fetch_settlement_breakdown_details(session.page, (today - timedelta(days=30)).isoformat(), today.isoformat())
print('상세 응답 개수:', len(details))
tagged = sum(1 for d in details if d['depositDueDate'] is not None)
print('depositDueDate 매핑된 개수:', tagged, '/', len(details))
by_date = map_settlement_breakdown_by_date(details)
for d, amounts in sorted(by_date.items()):
    print(d, amounts)
session.close()
"
```

Expected: `상세 응답 개수`가 최근 30일치 정산 배치 개수와 비슷하게 찍히고(정산내역 화면에서 직접 눈으로 센 카드 개수와 대조), `depositDueDate 매핑된 개수`가 전체와 같아야 한다(0개 있으면 giveId → depositDueDate 매핑이 깨진 것 — Step 1의 URL 파싱이나 summary 응답 캡처를 다시 점검). 날짜별 4개 카테고리 값이 전부 0 이상의 합리적인 숫자로 찍히는지 확인한다. 카드 클릭이 일부만 잡히면(예: 30개 카드 중 10개만 상세 응답이 옴) `_click_all_settlement_cards_on_page`의 선택자를 다시 점검한다 — 특히 같은 날짜 카드 중복, 이전 카드의 펼쳐진 상세가 다음 카드 위치를 밀어내는지.

- [ ] **Step 3: 전체 백엔드 테스트 재확인 (회귀 없음 확인용 — 이 태스크는 새 자동 테스트를 추가하지 않는다)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/scrapers/baemin_stats.py
git commit -m "feat: 정산 상세 화면의 카드별 organic 응답을 가로채는 fetch 함수 추가"
```

---

### Task 4: `review_sync.py` 통합 — upsert 확장 + "데이터 동기화"에 편입

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 1의 `DailySettlement` 신규 필드 4개. Task 2의 `map_settlement_breakdown_by_date`. Task 3의 `fetch_settlement_breakdown_details`, `BaeminStatsScrapeError`(이미 있음).
- Produces: `upsert_daily_settlement`에 신규 kwarg 4개 추가(기존 시그니처는 그대로 유지, 하위 호환). `_run_sync`가 내부적으로 호출한다.

- [ ] **Step 1: `upsert_daily_settlement` 확장에 대한 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 끝에 추가:

```python
def test_upsert_daily_settlement_sets_breakdown_columns_on_new_row(db_session, seeded_user, platforms):
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-12",
        commission_amount=131_402, delivery_fee_amount=210_800,
        customer_discount_amount=271_760, ad_cost_amount=48_095,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-12",
    ).one()
    assert row.commission_amount == 131_402
    assert row.delivery_fee_amount == 210_800
    assert row.customer_discount_amount == 271_760
    assert row.ad_cost_amount == 48_095


def test_upsert_daily_settlement_breakdown_none_leaves_existing_value_untouched(db_session, seeded_user, platforms):
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date=date(2026, 8, 12),
        sales_amount=0, deposit_amount=0, commission_amount=999,
    )
    db_session.add(existing)
    db_session.commit()

    # sales_amount만 갱신하는 흔한 호출 — commission_amount는 안 건드려야 한다.
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-12", sales_amount=5_000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-12",
    ).one()
    assert row.sales_amount == 5_000
    assert row.commission_amount == 999  # 안 건드림
```

`backend/tests/test_review_sync.py`의 기존 import 블록(`from app.review_sync import ...` 줄)에 이미 `upsert_daily_settlement`와 `DailySettlement`가 임포트돼 있으므로 이 파일에는 새 import가 필요 없다. `from datetime import date`도 파일 상단에 이미 있다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "upsert_daily_settlement_sets_breakdown or upsert_daily_settlement_breakdown_none"`
Expected: FAIL — `TypeError: upsert_daily_settlement() got an unexpected keyword argument 'commission_amount'`

- [ ] **Step 3: `backend/app/review_sync.py`의 `upsert_daily_settlement` 확장**

```python
def upsert_daily_settlement(
    db: Session, store_id: int, platform_id: int, settle_date: str,
    *, sales_amount: int | None = None, deposit_amount: int | None = None,
) -> None:
```

를 다음으로 교체:

```python
def upsert_daily_settlement(
    db: Session, store_id: int, platform_id: int, settle_date: str,
    *, sales_amount: int | None = None, deposit_amount: int | None = None,
    commission_amount: int | None = None, delivery_fee_amount: int | None = None,
    customer_discount_amount: int | None = None, ad_cost_amount: int | None = None,
) -> None:
```

`upsert_daily_settlement` 함수 본문 전체(`d = date.fromisoformat(settle_date)`부터 마지막 `existing.deposit_amount = deposit_amount` 줄까지)를 다음으로 교체:

```python
    d = date.fromisoformat(settle_date)
    existing = db.scalar(
        select(DailySettlement).where(
            DailySettlement.store_id == store_id,
            DailySettlement.platform_id == platform_id,
            DailySettlement.settle_date == d,
        )
    )
    if existing is None:
        db.add(DailySettlement(
            store_id=store_id, platform_id=platform_id, settle_date=d,
            sales_amount=sales_amount or 0, deposit_amount=deposit_amount or 0,
            commission_amount=commission_amount, delivery_fee_amount=delivery_fee_amount,
            customer_discount_amount=customer_discount_amount, ad_cost_amount=ad_cost_amount,
        ))
        # autoflush=False(app.db.SessionLocal)라 flush 없이는 이 세션의 다음
        # select()가 방금 add()한 행을 못 본다 — 같은 날짜를 매출(주문내역)과
        # 입금(정산내역)이 각각 다른 호출로 건드리는 흔한 경우(오늘 날짜는
        # 항상 두 소스 모두의 대상), flush 없이는 두 번째 호출도 "없음"으로
        # 보고 중복 INSERT를 시도해 UniqueViolation이 난다.
        db.flush()
        return
    if sales_amount is not None:
        existing.sales_amount = sales_amount
    if deposit_amount is not None:
        existing.deposit_amount = deposit_amount
    if commission_amount is not None:
        existing.commission_amount = commission_amount
    if delivery_fee_amount is not None:
        existing.delivery_fee_amount = delivery_fee_amount
    if customer_discount_amount is not None:
        existing.customer_discount_amount = customer_discount_amount
    if ad_cost_amount is not None:
        existing.ad_cost_amount = ad_cost_amount
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "upsert_daily_settlement_sets_breakdown or upsert_daily_settlement_breakdown_none"`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 5: `_run_sync` 통합에 대한 실패하는 테스트 작성**

`sync_setup` 픽스처(현재 `fetch_shop_stats`/`fetch_account_settlement`/`fetch_current_month_orders`/`fetch_brand_click_metrics`에 안전한 기본값을 monkeypatch하는 부분)에 한 줄을 추가한다. 다음 블록:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", lambda page, shop_no, months: [])
```

를 다음으로 교체:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", lambda page, shop_no, months: ([], []))
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [])
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", lambda page, shop_no, months: [])
    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", lambda page, start_date, end_date: [])
```

`backend/tests/test_review_sync.py` 파일 끝에 추가:

```python
_BREAKDOWN_DETAIL = {
    "giveId": 531969790, "depositDueDate": "2026-08-12",
    "giveAmount": 904812,
    "baemin1Details": {
        "giveAmount": 936472,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -102741},
            "benefitsAmount": {"total": -266760},
        },
        "delivery": {"deliverySupplyPrice": {"total": -210800}},
        "extra": {"paymentFee": {"total": -27329}},
    },
    "baeminDetails": {
        "giveAmount": 16435,
        "orderBrokerage": {
            "serviceFeeAmount": {"total": -1081},
            "benefitsAmount": {"total": -5000},
        },
        "delivery": {"deliverySupplyPrice": {"total": 0}},
        "extra": {"paymentFee": {"total": -251}},
    },
    "etcDetails": {"total": 0},
    "cpcDetails": {"total": -48095},
}


def test_sync_upserts_settlement_breakdown_columns(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_settlement_breakdown_details",
        lambda page, start_date, end_date: [_BREAKDOWN_DETAIL],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-12",
    ).one()
    assert row.commission_amount == 131_402
    assert row.delivery_fee_amount == 210_800
    assert row.customer_discount_amount == 271_760
    assert row.ad_cost_amount == 48_095


def test_sync_isolates_settlement_breakdown_failure_from_deposit(db_session, sync_setup, monkeypatch):
    """상세 수집이 실패해도(예: 카드 클릭 실패) 이미 별도로 성공한
    deposit_amount(summary 기반)에는 영향이 없어야 한다 — 설계 문서 에러
    처리 표."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import BaeminStatsScrapeError

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [
            {"contents": [{"giveId": 531969790, "depositDueDate": "2026-08-12", "giveAmount": 904812}], "totalSize": 1},
        ],
    )

    def _raise(page, start_date, end_date):
        raise BaeminStatsScrapeError("정산 상세 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _raise)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-12",
    ).one()
    assert row.deposit_amount == 904_812  # summary 기반 입금액은 영향 없음
    assert row.commission_amount is None  # 상세는 실패했으니 NULL 유지
    assert "정산 상세" in job.error_message
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k settlement_breakdown`
Expected: FAIL — `_run_sync`가 아직 `fetch_settlement_breakdown_details`를 호출하지 않으므로 `commission_amount`가 채워지지 않아 assert 실패

- [ ] **Step 7: `_run_sync`에 정산 상세 동기화 추가**

`backend/app/review_sync.py` 상단 import 블록의

```python
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

를 다음으로 교체:

```python
from scrapers.baemin_stats import (
    BaeminStatsScrapeError,
    compute_repurchase_rates,
    fetch_account_settlement,
    fetch_current_month_orders,
    fetch_settlement_breakdown_details,
    fetch_shop_stats,
    map_deposits_by_date,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    map_settlement_breakdown_by_date,
    recent_months,
)
```

정산(입금) 동기화 블록(`except Exception as e: stats_errors.append(f"정산(입금) 동기화 실패: {e}")`로 끝나는 블록) 바로 다음, "우리가게클릭" 루프(`# 우리가게클릭은 매출/입금/재주문율과 달리 계정 전체로 합산하지`로 시작하는 주석) 앞에 삽입:

```python
        try:
            detail_window_start = today - timedelta(days=30)
            breakdown_details = fetch_settlement_breakdown_details(
                session.page, detail_window_start.isoformat(), today.isoformat(),
            )
            breakdown_by_date = map_settlement_breakdown_by_date(breakdown_details)
            for settle_date, amounts in breakdown_by_date.items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date,
                    commission_amount=amounts["commission_amount"],
                    delivery_fee_amount=amounts["delivery_fee_amount"],
                    customer_discount_amount=amounts["customer_discount_amount"],
                    ad_cost_amount=amounts["ad_cost_amount"],
                )
            if breakdown_by_date:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"정산 상세(수수료/배달비/고객할인/우가클비용) 동기화 실패: {e}")

```

(이 블록은 90일 창의 `deposit_amount` upsert와 별개다 — 30일 창만 조회하므로 그보다 오래된 날짜의 신규 컬럼 4개는 항상 NULL로 남는다, 설계 문서 "동기화 흐름" 절 참고.)

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k settlement_breakdown`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 10: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 데이터 동기화에 정산 상세(수수료/배달비/고객할인/우가클비용) upsert 통합"
```

---

### Task 5: 백엔드 엔드포인트 — `GET /sales/breakdown`에 실측값 반영

**Files:**
- Modify: `backend/app/routers/sales.py`
- Test: `backend/tests/test_sales.py`

**Interfaces:**
- Consumes: Task 1의 `DailySettlement` 신규 필드 4개.
- Produces: `/sales/breakdown` 응답의 `platforms[]` 각 항목에 `is_estimate: bool` 추가. `is_estimate: true`면 기존 필드(`commission_estimate`, `payment_fee_estimate`, `net_estimate`) 그대로. `is_estimate: false`면 `commission_amount`, `delivery_fee_amount`, `customer_discount_amount`, `ad_cost_amount`, `misc_amount`(계산값) 5개. `actual_deposit`은 두 경우 모두 그대로 있음. Task 6(프론트)이 이 응답을 그대로 소비한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_sales.py`의 기존 `test_sales_breakdown_computes_commission_from_platform_rate` 테스트 마지막 줄(`assert row["actual_deposit"] == 89_200 ...`) 다음에 한 줄 추가:

```python
    assert row["is_estimate"] is True  # 신규 컬럼이 전부 NULL이라 추정치로 폴백
```

같은 파일 끝에 추가:

```python
def test_sales_breakdown_uses_real_values_when_columns_filled(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=200_000, deposit_amount=150_000,
        commission_amount=20_000, delivery_fee_amount=10_000,
        customer_discount_amount=15_000, ad_cost_amount=3_000,
    ))
    db_session.commit()

    res = client.get("/sales/breakdown?period=day", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["is_estimate"] is False
    assert row["sales_amount"] == 200_000
    assert row["commission_amount"] == 20_000
    assert row["delivery_fee_amount"] == 10_000
    assert row["customer_discount_amount"] == 15_000
    assert row["ad_cost_amount"] == 3_000
    # misc = 200000 - 20000 - 10000 - 15000 - 3000 - 150000
    assert row["misc_amount"] == 2_000
    assert row["actual_deposit"] == 150_000
    assert "commission_estimate" not in row  # 추정치 필드는 실측 응답에 안 섞임


def test_sales_breakdown_partial_period_data_still_falls_back_to_estimate(client, db_session, seeded_user, platforms, auth_headers):
    """기간 안에 신규 컬럼이 채워진 날짜가 하나도 없으면(예: 30일 상세
    수집 범위 밖) 전체 기간을 추정치로 폴백한다."""
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(
        store_id=store.id, platform_id=platform.id,
        settle_date=date.today() - timedelta(days=40),
        sales_amount=100_000, deposit_amount=89_200,
    ))
    db_session.commit()

    res = client.get("/sales/breakdown?period=month", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["is_estimate"] is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_sales.py -v -k breakdown`
Expected: FAIL — `KeyError: 'is_estimate'`

- [ ] **Step 3: `backend/app/routers/sales.py`의 `sales_breakdown` 함수 교체**

```python
@router.get("/sales/breakdown")
def sales_breakdown(
    period: Period = "week",
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """매출분석 카드. 플랫폼별 매출 → 차감 항목 → 순정산액.

    배민 행은 정산 상세(수수료/배달비/고객할인/우가클비용) 동기화가 된
    기간이면 실측값(`is_estimate: False`)을, 아직 없으면(신규 컬럼이 전부
    NULL) 요율 기반 추정치(`is_estimate: True`)로 폴백한다. 요기요/쿠팡이츠는
    실측 컬럼을 채우지 않으므로 항상 추정치다. "기타"(misc_amount)는
    저장하지 않고 sales_amount − 4개 실측 카테고리 − actual_deposit로
    계산한다(설계 문서 "데이터 모델 변경" 절 — 정규화 원칙, 서로 다른
    배민 화면 간 오차를 그대로 드러내는 게 의도된 동작).
    """
    sid = store_id or get_user_default_store_id(user, db)
    start, end = _period_range(period)

    rows = db.execute(
        select(
            Platform.id, Platform.name, Platform.default_commission_rate,
            func.coalesce(func.sum(DailySettlement.sales_amount), 0),
            func.coalesce(func.sum(DailySettlement.deposit_amount), 0),
            func.coalesce(func.sum(DailySettlement.commission_amount), 0),
            func.coalesce(func.sum(DailySettlement.delivery_fee_amount), 0),
            func.coalesce(func.sum(DailySettlement.customer_discount_amount), 0),
            func.coalesce(func.sum(DailySettlement.ad_cost_amount), 0),
            func.count(DailySettlement.commission_amount),
        )
        .join(DailySettlement, DailySettlement.platform_id == Platform.id)
        .where(DailySettlement.store_id == sid, DailySettlement.settle_date.between(start, end))
        .group_by(Platform.id, Platform.name, Platform.default_commission_rate)
        .order_by(Platform.id)
    ).all()

    result = []
    for (platform_id, name, commission_rate, sales, actual_deposit,
         real_commission, real_delivery, real_discount, real_ad_cost, real_rows_count) in rows:
        sales = int(sales)
        actual_deposit = int(actual_deposit)
        is_estimate = real_rows_count == 0
        if is_estimate:
            rate = float(commission_rate or 0)
            commission = round(sales * rate)
            payment_fee = round(sales * PAYMENT_FEE_RATE)
            result.append({
                "platform_id": platform_id,
                "platform_name": name,
                "sales_amount": sales,
                "is_estimate": True,
                "commission_estimate": commission,
                "payment_fee_estimate": payment_fee,
                "net_estimate": sales - commission - payment_fee,
                "actual_deposit": actual_deposit,
            })
        else:
            commission = int(real_commission)
            delivery = int(real_delivery)
            discount = int(real_discount)
            ad_cost = int(real_ad_cost)
            misc = sales - commission - delivery - discount - ad_cost - actual_deposit
            result.append({
                "platform_id": platform_id,
                "platform_name": name,
                "sales_amount": sales,
                "is_estimate": False,
                "commission_amount": commission,
                "delivery_fee_amount": delivery,
                "customer_discount_amount": discount,
                "ad_cost_amount": ad_cost,
                "misc_amount": misc,
                "actual_deposit": actual_deposit,
            })
    return {"period": period, "from_date": start.isoformat(), "to_date": end.isoformat(), "platforms": result}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_sales.py -v -k breakdown`
Expected: 4개 테스트 전부 PASS(기존 1개 + 신규 2개 + 기존 테스트에 추가한 assert 1줄)

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/sales.py backend/tests/test_sales.py
git commit -m "feat: /sales/breakdown이 배민 정산 상세 실측값을 is_estimate로 분기해 반환"
```

---

### Task 6: 프론트엔드 — `SalesBreakdownModal` 실측/추정 분기 렌더링

**Files:**
- Modify: `frontend/src/app/(app)/dashboard/page.tsx`

**Interfaces:**
- Consumes: Task 5의 `GET /sales/breakdown` 응답(`is_estimate` 분기, 실측 5개 필드 또는 추정 3개 필드).
- Produces: 없음(터미널 UI 컴포넌트).

- [ ] **Step 1: `BreakdownRow` 타입을 판별 유니언으로 교체**

`frontend/src/app/(app)/dashboard/page.tsx`에서:

```typescript
type BreakdownRow = { platform_name: string; sales_amount: number; commission_estimate: number; payment_fee_estimate: number; net_estimate: number; actual_deposit: number };
```

를 다음으로 교체:

```typescript
type BreakdownRowEstimate = {
  platform_id: number; platform_name: string; sales_amount: number; is_estimate: true;
  commission_estimate: number; payment_fee_estimate: number; net_estimate: number; actual_deposit: number;
};
type BreakdownRowActual = {
  platform_id: number; platform_name: string; sales_amount: number; is_estimate: false;
  commission_amount: number; delivery_fee_amount: number; customer_discount_amount: number;
  ad_cost_amount: number; misc_amount: number; actual_deposit: number;
};
type BreakdownRow = BreakdownRowEstimate | BreakdownRowActual;
```

- [ ] **Step 2: `SalesBreakdownModal` 렌더링 분기 추가**

```tsx
function SalesBreakdownModal({ storeId, period }: { storeId: number; period: Period }) {
  const [data, setData] = useState<{ platforms: BreakdownRow[] } | null>(null);
  useEffect(() => {
    apiGet<{ platforms: BreakdownRow[] }>(`/sales/breakdown?period=${period}&store_id=${storeId}`).then(setData);
  }, [storeId, period]);

  if (!data) return <p className="text-sm text-muted">불러오는 중...</p>;
  if (data.platforms.length === 0) return <p className="text-sm text-muted">해당 기간 매출 데이터가 없습니다.</p>;

  return (
    <div className="space-y-4">
      <p className="rounded-lg bg-surface-2 p-3 text-xs text-muted">
        배민은 정산 상세(수수료/배달비/고객할인/우가클비용)가 동기화된 기간이면 실제 차감
        내역을 보여줍니다. 아직 없으면 플랫폼 기본 요율로 추정한 값입니다.
      </p>
      {data.platforms.map((p) => (
        <div key={p.platform_name} className="rounded-lg border border-border-subtle p-4">
          <p className="mb-2 text-sm font-medium text-accent">{p.platform_name}</p>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between"><dt className="text-muted">매출액</dt><dd>{won(p.sales_amount)}</dd></div>
            {p.is_estimate ? (
              <>
                <div className="flex justify-between text-danger"><dt>− 중개수수료(추정)</dt><dd>−{won(p.commission_estimate)}</dd></div>
                <div className="flex justify-between text-danger"><dt>− 결제수수료(추정)</dt><dd>−{won(p.payment_fee_estimate)}</dd></div>
                <div className="flex justify-between border-t border-border-subtle pt-1 font-semibold"><dt>추정 정산액</dt><dd>{won(p.net_estimate)}</dd></div>
              </>
            ) : (
              <>
                <div className="flex justify-between text-danger"><dt>− 수수료</dt><dd>−{won(p.commission_amount)}</dd></div>
                <div className="flex justify-between text-danger"><dt>− 배달비</dt><dd>−{won(p.delivery_fee_amount)}</dd></div>
                <div className="flex justify-between text-danger"><dt>− 고객할인</dt><dd>−{won(p.customer_discount_amount)}</dd></div>
                <div className="flex justify-between text-danger"><dt>− 우가클비용(광고비)</dt><dd>−{won(p.ad_cost_amount)}</dd></div>
                <div className="flex justify-between text-muted"><dt>− 기타</dt><dd>−{won(p.misc_amount)}</dd></div>
              </>
            )}
            <div className="flex justify-between text-success"><dt>실제 입금액</dt><dd>{won(p.actual_deposit)}</dd></div>
          </dl>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 로컬에서 실제 계정으로 확인**

로컬 백엔드(`:8000`)/프론트(`:3000`) 실행 후, "가게 연결" 화면에서 "데이터 동기화"를 실행해 Task 4까지 통합된 정산 상세 동기화가 끝난 뒤, 대시보드 "매출 분석" 카드를 열어 배민 행이 5줄(수수료/배달비/고객할인/우가클비용/기타) + 실제입금액으로 나오는지, 요기요/쿠팡이츠 행은 기존 2줄 추정 그대로인지 브라우저로 직접 확인한다.

- [ ] **Step 5: 커밋**

```bash
git add "frontend/src/app/(app)/dashboard/page.tsx"
git commit -m "feat: 매출 분석 카드가 배민 정산 상세 실측값을 5줄로 보여주도록 분기 렌더링"
```

---

## CLAUDE.md 갱신 (마지막 태스크 이후, 최종 리뷰 전)

"배민 매출·입금·재주문율 연동 (예외 허용)" 절과 "배민 우리가게클릭(우가클) 브랜드별 실데이터 연동 (예외 허용)" 절 사이에, 스펙 문서의 "CLAUDE.md 갱신" 절 내용대로 "배민 정산 상세(수수료/배달비/고객할인/우가클비용) 연동 (예외 허용)" 절을 추가한다. 별도 태스크로 분리하지 않고 Task 6 완료 후 전체 리뷰 단계에서 함께 처리한다(이전 두 실데이터 연동 계획과 동일한 관례).
