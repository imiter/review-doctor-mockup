# 배민 매출·입금·재주문율 실데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "가게 연결" 화면의 "리뷰 동기화"(→ "데이터 동기화") 버튼이 리뷰뿐 아니라 매출·입금·재주문율까지 실제 배민 사장님광장 데이터로 가져와 대시보드/매출 화면의 Mock 값을 실데이터로 교체한다.

**Architecture:** 기존 배민 리뷰 스크래핑과 같은 인증 세션(`backend/scrapers/baemin_auth.py`의 `login()`)을 재사용한다 — 재로그인 없음. 같은 organic 응답 가로채기 방식(`page.on("response", ...)`)으로 가게통계 화면(브랜드별 매출/재주문율)과 정산내역 화면(계정 전체 입금)의 API 응답을 캡처한다. 매출/재주문율은 4개 브랜드 응답을 날짜별로 합산해 계정 전체 숫자 하나로 만들고(브랜드별 분리 없음), 기존 `daily_settlements`/`repurchase_metrics` 스키마를 그대로 upsert 대상으로 쓴다 — 스키마 변경 없음.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API), Next.js App Router.

## Global Constraints

- 배민만. 쿠팡이츠/요기요는 이번에도 범위 밖.
- 매출/입금/재주문율은 브랜드(치밥대장 등)별로 나누지 않는다 — 계정(사업자) 전체 합산 하나로만 저장한다. 스키마에 `platform_shop_no` 같은 브랜드 구분 컬럼을 추가하지 않는다.
- 입금 상태(예정/확정) 구분 컬럼은 만들지 않는다 — 배민이 주는 `giveAmount`를 상태와 무관하게 그대로 저장한다.
- 매출/입금은 이번 달 포함 최근 3개월만 백필한다. 재주문율은 배민 API 자체가 고정 최근 7일 창만 주므로 소급 불가 — 동기화할 때마다 최근 7일 스냅샷만 갱신된다.
- 새 엔드포인트를 만들지 않는다 — 기존 `POST /store-connections/baemin/sync-reviews`(엔드포인트/테이블 이름은 그대로 유지) 안에서 확장한다. 프론트 버튼 라벨만 "데이터 동기화"로 바꾼다.
- 리뷰 API와 마찬가지로 이 API들도 로그인 세션 쿠키 + 동적 서명 헤더가 있어야 응답이 온다 — 직접 HTTP 호출(APIRequestContext, raw fetch)은 403/CORS로 막힌다. 반드시 인증된 `page`가 실제 화면을 이동하며 organic하게 발생시키는 응답을 `page.on("response", ...)`로 가로챈다.
- 로그인 자동화·실제 화면 DOM 조사는 실제 배민 계정이 필요해 자동화된 pytest로 덮지 않는다 — 순수 매핑 함수와 `_run_sync`의 오케스트레이션 로직만 촘촘히 테스트하고, 실제 화면 상호작용(월 선택 드롭다운, 날짜 선택 캘린더)은 실 계정으로 수동 검증한다. 자격증명은 환경변수로만 다루고 로그에도 남기지 않는다.
- 이 프로젝트는 Alembic을 쓰지 않는다 — 이번 작업은 스키마 변경이 없으므로 `schema.sql`도 건드리지 않는다.
- 참고 스펙: `docs/superpowers/specs/2026-08-11-baemin-sales-deposit-repurchase-design.md`

---

### Task 1: 순수 매핑 함수 — 매출/입금/재주문율 API 응답 → 날짜별 집계

**Files:**
- Create: `backend/scrapers/baemin_stats.py`
- Test: `backend/tests/test_baemin_stats.py`

**Interfaces:**
- Consumes: 없음(외부 의존 없는 순수 함수).
- Produces: `map_sales_by_date(responses: list[dict]) -> dict[str, int]`, `map_deposits_by_date(responses: list[dict]) -> dict[str, int]`, `map_repurchase_by_date(responses: list[dict]) -> dict[str, dict[str, int]]`(키: `new_orders`, `repeat_orders`), `compute_repurchase_rates(by_date: dict[str, dict[str, int]]) -> dict[str, dict]`(키: `new_orders`, `repeat_orders`, `rate_raw`, `rate_adjusted`). Task 3이 이 4개 함수를 그대로 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_baemin_stats.py` 신규 생성:

```python
from scrapers.baemin_stats import (
    compute_repurchase_rates,
    map_deposits_by_date,
    map_repurchase_by_date,
    map_sales_by_date,
)

# 실 계정(치밥대장, 곱도리탕 등)에서 확인한 실제 statistics/orders/summary 응답 형태.
_SALES_RESPONSE_BRAND_A = {
    "graph": {"data": [
        {"x": "2026-07-30", "y": 60200.0},
        {"x": "2026-07-31", "y": 102800.0},
    ]},
    "orderAmount": 163000.0,
    "orderCount": 5,
}
_SALES_RESPONSE_BRAND_B = {
    "graph": {"data": [
        {"x": "2026-07-30", "y": 15000.0},
        {"x": "2026-07-31", "y": 0},
    ]},
    "orderAmount": 15000.0,
    "orderCount": 1,
}


def test_map_sales_by_date_sums_across_brands_for_same_date():
    result = map_sales_by_date([_SALES_RESPONSE_BRAND_A, _SALES_RESPONSE_BRAND_B])
    assert result == {"2026-07-30": 75200, "2026-07-31": 102800}


def test_map_sales_by_date_rounds_fractional_amounts():
    resp = {"graph": {"data": [{"x": "2026-07-30", "y": 100.4}]}, "orderAmount": 100.4, "orderCount": 1}
    assert map_sales_by_date([resp]) == {"2026-07-30": 100}


def test_map_sales_by_date_empty_list_returns_empty_dict():
    assert map_sales_by_date([]) == {}


# 실 계정에서 확인한 실제 settle/history/summary 응답 형태(페이지 2개로 흉내).
_SETTLE_PAGE_1 = {
    "foodSuccess": True, "commerceSuccess": True,
    "contents": [
        {"giveId": 531969790, "depositDueDate": "2026-08-12", "settleCode": "FOOD",
         "giveStatus": "REQUEST", "giveStartDate": "2026-08-07", "giveEndDate": "2026-08-09",
         "giveAmount": 904812},
        {"giveId": 531748522, "depositDueDate": "2026-08-11", "settleCode": "FOOD",
         "giveStatus": "REQUEST", "giveStartDate": "2026-08-06", "giveEndDate": "2026-08-06",
         "giveAmount": 168431},
    ],
    "totalSize": 3,
}
_SETTLE_PAGE_2 = {
    "foodSuccess": True, "commerceSuccess": True,
    "contents": [
        {"giveId": 531600000, "depositDueDate": "2026-08-12", "settleCode": "FOOD",
         "giveStatus": "GIVEN", "giveStartDate": "2026-08-05", "giveEndDate": "2026-08-05",
         "giveAmount": 50000},
    ],
    "totalSize": 3,
}


def test_map_deposits_by_date_sums_multiple_batches_sharing_same_due_date():
    # 08-12에 배치가 2건(904,812원 + 50,000원) 겹치는 상황 — 같은 날짜로 합산돼야 한다.
    result = map_deposits_by_date([_SETTLE_PAGE_1, _SETTLE_PAGE_2])
    assert result == {"2026-08-12": 954812, "2026-08-11": 168431}


def test_map_deposits_by_date_ignores_give_status():
    # 상태(예정/확정)는 구분하지 않고 giveAmount를 그대로 합산한다(설계 결정).
    result = map_deposits_by_date([_SETTLE_PAGE_2])
    assert result == {"2026-08-12": 50000}


def test_map_deposits_by_date_empty_contents_returns_empty_dict():
    assert map_deposits_by_date([{"contents": [], "totalSize": 0}]) == {}


# 실 계정에서 확인한 실제 crmInfo 응답의 newReorderSummary 형태(브랜드 2개).
_CRM_RESPONSE_BRAND_A = {
    "orderSummary": {"orderCount": 2, "orderPrice": 40200.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 1,
        "timeNewGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 1}, {"x": "2026-08-05", "y": 0},
        ]},
        "timeReorderGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 1},
        ]},
    },
}
_CRM_RESPONSE_BRAND_B = {
    "orderSummary": {"orderCount": 1, "orderPrice": 15000.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 0,
        "timeNewGraph": {"data": [
            {"x": "2026-08-03", "y": 1}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 0},
        ]},
        "timeReorderGraph": {"data": [
            {"x": "2026-08-03", "y": 0}, {"x": "2026-08-04", "y": 0}, {"x": "2026-08-05", "y": 0},
        ]},
    },
}


def test_map_repurchase_by_date_sums_new_and_repeat_across_brands():
    result = map_repurchase_by_date([_CRM_RESPONSE_BRAND_A, _CRM_RESPONSE_BRAND_B])
    assert result == {
        "2026-08-03": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-04": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-05": {"new_orders": 0, "repeat_orders": 1},
    }


def test_map_repurchase_by_date_empty_list_returns_empty_dict():
    assert map_repurchase_by_date([]) == {}


def test_compute_repurchase_rates_raw_is_same_day_ratio():
    by_date = {
        "2026-08-01": {"new_orders": 3, "repeat_orders": 1},
        "2026-08-02": {"new_orders": 1, "repeat_orders": 3},
    }
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-01"]["rate_raw"] == 0.25   # 1 / (3+1)
    assert result["2026-08-02"]["rate_raw"] == 0.75   # 3 / (1+3)


def test_compute_repurchase_rates_raw_is_zero_when_no_orders_that_day():
    by_date = {"2026-08-01": {"new_orders": 0, "repeat_orders": 0}}
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-01"]["rate_raw"] == 0.0


def test_compute_repurchase_rates_adjusted_sums_trailing_7_days_inclusive():
    # seed.sql의 Mock 생성 로직(ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)과
    # 동일한 "당일 포함 최근 7일" 윈도우여야 한다.
    by_date = {f"2026-08-{d:02d}": {"new_orders": 1, "repeat_orders": 0} for d in range(1, 9)}
    by_date["2026-08-08"] = {"new_orders": 0, "repeat_orders": 7}  # 8일째만 전부 재주문

    result = compute_repurchase_rates(by_date)
    # 8/8 기준 최근 7일 윈도우 = 8/2~8/8 (7일치): new=6(2~7일, 각 1건) + repeat=7(8일) = 13건 중 repeat 7건
    assert result["2026-08-08"]["rate_adjusted"] == round(7 / 13, 4)


def test_compute_repurchase_rates_adjusted_window_shrinks_near_start_of_data():
    # 데이터가 3일치뿐이면 윈도우는 그 3일 전체로 축소된다(7일 채우지 못해도 에러 아님).
    by_date = {
        "2026-08-01": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-02": {"new_orders": 1, "repeat_orders": 0},
        "2026-08-03": {"new_orders": 0, "repeat_orders": 1},
    }
    result = compute_repurchase_rates(by_date)
    assert result["2026-08-03"]["rate_adjusted"] == round(1 / 3, 4)  # 3일 전체: new=2, repeat=1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.baemin_stats'`

- [ ] **Step 3: `backend/scrapers/baemin_stats.py` 구현**

```python
"""배민 사장님광장의 가게통계(매출/재주문율)·정산내역(입금) API 응답을
날짜별로 집계하는 순수 함수. 실제 organic 응답 캡처는 이 파일이 아니라
`fetch_shop_stats`/`fetch_account_settlement`(다음 태스크에서 추가)가
담당한다 — 이 모듈은 이미 받아온 raw dict만 다룬다.

브랜드(치밥대장 등)별로 분리하지 않고 전부 날짜 단위로 합산한다 — 설계
결정(계정 전체 합산만 지원, 입금이 애초에 브랜드별로 안 나뉘어 나오기
때문). 세 함수 모두 여러 브랜드/여러 달/여러 페이지에 걸친 raw 응답
리스트를 받아 하나의 날짜별 dict로 합친다.
"""


def map_sales_by_date(responses: list[dict]) -> dict[str, int]:
    """`GET /v3/statistics/orders/summary` 응답들의 `graph.data[].{x,y}`를
    날짜별로 합산한다. 브랜드마다, 그리고 월마다 한 번씩 호출한 응답을 전부
    이 리스트에 담아 넘긴다."""
    totals: dict[str, int] = {}
    for resp in responses:
        for point in resp["graph"]["data"]:
            date_str = point["x"]
            totals[date_str] = totals.get(date_str, 0) + round(point["y"])
    return totals


def map_deposits_by_date(responses: list[dict]) -> dict[str, int]:
    """`GET /v3/settle/history/summary` 응답들의 `contents[].{depositDueDate,
    giveAmount}`를 날짜별로 합산한다. 같은 depositDueDate에 배치가 여러 건
    겹치면 합산한다. `giveStatus`(예정/확정)는 구분하지 않는다(설계 결정) —
    상태와 무관하게 금액을 그대로 더한다. 페이지네이션이 있으면 여러 페이지
    응답을 전부 이 리스트에 담아 넘긴다."""
    totals: dict[str, int] = {}
    for resp in responses:
        for batch in resp["contents"]:
            date_str = batch["depositDueDate"]
            totals[date_str] = totals.get(date_str, 0) + batch["giveAmount"]
    return totals


def map_repurchase_by_date(responses: list[dict]) -> dict[str, dict[str, int]]:
    """`GET /v3/dashboard/crmInfo` 응답들의
    `newReorderSummary.timeNewGraph`/`timeReorderGraph`(각각 날짜별 신규/재주문
    건수)를 날짜별로 합산한다. 브랜드마다 한 번씩 호출한 응답을 전부 이
    리스트에 담아 넘긴다."""
    totals: dict[str, dict[str, int]] = {}

    def _bucket(date_str: str) -> dict[str, int]:
        return totals.setdefault(date_str, {"new_orders": 0, "repeat_orders": 0})

    for resp in responses:
        summary = resp["newReorderSummary"]
        for point in summary["timeNewGraph"]["data"]:
            _bucket(point["x"])["new_orders"] += point["y"]
        for point in summary["timeReorderGraph"]["data"]:
            _bucket(point["x"])["repeat_orders"] += point["y"]
    return totals


def compute_repurchase_rates(by_date: dict[str, dict[str, int]]) -> dict[str, dict]:
    """날짜별 new_orders/repeat_orders 집계에서 rate_raw(당일 비율)와
    rate_adjusted(당일 포함 최근 7일 합산 비율)를 계산한다. seed.sql의 Mock
    생성 로직(`ROWS BETWEEN 6 PRECEDING AND CURRENT ROW`)과 동일한 윈도우
    정의를 쓴다 — repurchase_metrics 스키마 주석의 "보정 후 = 이전 7일 합산"
    문구가 가리키는 바로 그 정의."""
    sorted_dates = sorted(by_date.keys())
    result: dict[str, dict] = {}
    for i, d in enumerate(sorted_dates):
        new_orders = by_date[d]["new_orders"]
        repeat_orders = by_date[d]["repeat_orders"]
        total = new_orders + repeat_orders
        rate_raw = round(repeat_orders / total, 4) if total > 0 else 0.0

        window_dates = sorted_dates[max(0, i - 6):i + 1]
        window_new = sum(by_date[wd]["new_orders"] for wd in window_dates)
        window_repeat = sum(by_date[wd]["repeat_orders"] for wd in window_dates)
        window_total = window_new + window_repeat
        rate_adjusted = round(window_repeat / window_total, 4) if window_total > 0 else 0.0

        result[d] = {
            "new_orders": new_orders,
            "repeat_orders": repeat_orders,
            "rate_raw": rate_raw,
            "rate_adjusted": rate_adjusted,
        }
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v`
Expected: 13개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/scrapers/baemin_stats.py backend/tests/test_baemin_stats.py
git commit -m "feat: 매출/입금/재주문율 API 응답을 날짜별로 집계하는 순수 매핑 함수 추가"
```

---

### Task 2: 가게통계·정산내역 화면 organic 응답 캡처

**Files:**
- Modify: `backend/scrapers/baemin_stats.py` (Task 1에서 만든 파일에 fetch 함수 추가)

**Interfaces:**
- Consumes: Task 1의 `map_sales_by_date`/`map_deposits_by_date`/`map_repurchase_by_date`는 쓰지 않는다(호출자인 Task 3이 이 함수의 반환값을 그대로 Task 1 함수에 넘긴다). `baemin_auth.login()`이 반환한 `BaeminSession.page`(이미 인증된 살아있는 Playwright 페이지)를 그대로 받는다 — 재로그인하지 않는다.
- Produces: `fetch_shop_stats(page, shop_no: int, months: list[str]) -> tuple[list[dict], list[dict]]`(반환: `(sales_responses, crm_responses)` — 둘 다 Task 1의 `map_sales_by_date`/`map_repurchase_by_date`에 그대로 넘길 수 있는 raw dict 리스트), `fetch_account_settlement(page, start_date: str, end_date: str) -> list[dict]`(반환: `map_deposits_by_date`에 그대로 넘길 수 있는 페이지별 raw dict 리스트), `recent_months(count: int = 3) -> list[str]`(이번 달 포함 최근 `count`개월을 오래된 순 `"YYYY-MM"` 리스트로 반환), `BaeminStatsScrapeError`. Task 3이 이 함수들을 그대로 가져다 쓴다.

이 태스크는 배민 화면의 정확한 UI 조작 방식(월 선택 드롭다운, 날짜 선택 캘린더, 정산내역 페이지네이션)을 아직 확정할 수 없다 — 리뷰 스크래핑 때 "더보기" 버튼의 정확한 동작이 실 계정 조사로만 밝혀졌던 것과 같은 이유다. 이번 설계 조사에서 다음 두 가지는 이미 확인됐다:

- 가게통계 화면(`/shops/{shopNo}/stat`)은 `?month=YYYY-MM` 같은 URL 쿼리 파라미터를 읽지 않는다(직접 확인 — URL에 넣어도 무시하고 항상 이번 달 데이터를 요청한다). 화면 상단에 "7월 ⌄" 같은 월 선택 드롭다운이 있고, 이걸 실제로 클릭해서 조작해야 한다.
- 정산내역 화면(`/orders/billing`)도 `?startDate=&endDate=` URL 파라미터를 읽지 않는다(직접 확인). "날짜 직접 선택" 버튼을 눌러야 나오는 캘린더 위젯을 조작해야 한다. 좁은 기본 범위(3일)에서는 페이지네이션 UI가 안 보였는데, 90일 범위로 넓히면 항목이 많아져(하루~여러 건) 리스트 하단에 "더보기" 같은 컨트롤이 나타날 가능성이 높다 — 리뷰 리스트와 같은 패턴일 수 있다.

정확한 DOM 구조(선택자, 클릭 순서)는 아래 Step 1에서 실 계정으로 직접 조사해서 확정한다 — 추측해서 코드를 먼저 쓰지 않는다.

- [ ] **Step 1: 실 계정으로 월 선택 드롭다운·날짜 선택 캘린더·정산내역 페이지네이션 DOM 구조 조사**

이 프로젝트의 안전 원칙상 에이전트가 실제 비밀번호를 화면에 직접 입력하지 않지만, 이미 저장된 암호화 자격증명을 환경변수로 넘겨 헤드리스 Playwright로 로그인하는 것은 지금까지와 같은 방식으로 계속 가능하다. 스크래치패드에 1회성 진단 스크립트를 작성해서 실행하고, 조사가 끝나면 스크립트는 삭제한다(다른 배민 스크래퍼 개발 때와 동일한 관례).

조사할 것:
1. 가게통계 화면에서 "7월 ⌄" 같은 월 선택 요소를 클릭했을 때 나타나는 드롭다운의 실제 구조(네이티브 `<select>`인지, 커스텀 리스트인지) — `baemin_auth.py`의 `_discover_all_shops`가 매장 선택 `<select>`를 다뤘던 것과 같은 방식으로 `page.get_by_role("combobox")` 등으로 확인한다. 이전 달로 이동시키는 클릭 시퀀스를 확정한다.
2. 정산내역 화면의 "날짜 직접 선택" 버튼을 클릭했을 때 나타나는 캘린더 위젯의 구조 — 시작일/종료일을 각각 클릭해서 지정하는 방식인지, 프리셋이 있는지.
3. 정산내역을 90일 범위로 넓혔을 때 리스트 하단에 추가 로드 컨트롤(더보기 버튼/페이지 번호/스크롤)이 나타나는지, 나타난다면 그 컨트롤의 텍스트/구조.
4. 조사 도중에도 프로모션 backdrop(`page.get_by_test_id("backdrop")`)이 뜰 수 있다 — 리뷰 스크래퍼와 동일하게 각 클릭 시도 전에 방어적으로 확인·Escape 처리한다.

이 조사에서 확인한 정확한 선택자/클릭 순서를 다음 Step의 코드에 실제 값으로 반영한다.

- [ ] **Step 2: `backend/scrapers/baemin_stats.py`에 fetch 함수 추가**

Step 1에서 확인한 실제 선택자로 아래 템플릿의 `_select_month_dropdown`, `_open_date_range_picker`, `_set_date_range`를 채워서 작성한다. 정산내역에 페이지네이션 컨트롤이 있는 것으로 확인되면 `fetch_account_settlement` 안의 해당 주석 위치에 리뷰의 `_MAX_LOAD_MORE_CLICKS` 패턴과 같은 반복 클릭 로직을 직접 추가한다(별도 함수로 분리할지는 구현자 판단). 파일 맨 위(기존 매핑 함수들 앞)에 다음 import와 상수를 추가하고, 매핑 함수 뒤에 아래 함수들을 추가한다:

```python
from datetime import date, timedelta
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


class BaeminStatsScrapeError(Exception):
    pass


def _dismiss_backdrop_if_present(page) -> None:
    # baemin_reviews.py의 페이지네이션 클릭과 동일한 방어 패턴 — 프로모션
    # 모달이 조사 도중 언제든 다시 뜰 수 있다(실 계정으로 확인됨).
    if page.get_by_test_id("backdrop").count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def recent_months(count: int = 3) -> list[str]:
    """이번 달을 포함해 최근 `count`개월을 오래된 순으로 반환한다.
    예: 2026-08에 호출하면 ["2026-06", "2026-07", "2026-08"]. Task 3의
    `_run_sync`가 매출 백필 범위를 정할 때 그대로 가져다 쓴다."""
    today = date.today()
    months: list[str] = []
    y, m = today.year, today.month
    for _ in range(count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(months))


def _select_month_dropdown(page, month: str) -> None:
    """가게통계 화면의 월 선택 드롭다운을 `month`("YYYY-MM")로 바꾼다.
    Step 1 조사 결과로 실제 구현을 채운다 — 아래는 매장 선택 드롭다운
    (baemin_auth._discover_all_shops)과 같은 네이티브 select라고 가정한
    자리표시자다."""
    raise NotImplementedError("Step 1 조사 결과로 구현")


def fetch_shop_stats(page, shop_no: int, months: list[str]) -> tuple[list[dict], list[dict]]:
    """가게통계 화면(`/shops/{shop_no}/stat`)에서 매출(statistics/orders/summary)과
    재주문율(crmInfo) organic 응답을 가로챈다. `months`에 담긴 각 달마다 월
    선택 드롭다운을 조작해 그 달 데이터를 로드시킨다 — crmInfo는 월과 무관한
    고정 최근 7일 창이라 첫 로드에서만 캡처하고 이후 월 이동에서는 무시한다.
    """
    sales_responses: list[dict] = []
    crm_responses: list[dict] = []
    state = {"observed_sales_endpoint": False, "observed_crm_endpoint": False}

    def _on_response(response) -> None:
        url = response.url
        path = urlparse(url).path
        if "self-api.baemin.com" not in url:
            return
        if path == "/v3/statistics/orders/summary":
            state["observed_sales_endpoint"] = True
            if response.status == 200:
                try:
                    sales_responses.append(response.json())
                except Exception:
                    pass
        elif path == "/v3/dashboard/crmInfo":
            state["observed_crm_endpoint"] = True
            if response.status == 200 and not crm_responses:
                try:
                    crm_responses.append(response.json())
                except Exception:
                    pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/stat")
        except Exception as e:
            raise BaeminStatsScrapeError(f"가게통계 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)  # 첫 로드(이번 달 + crmInfo) 대기

        for month in months:
            _dismiss_backdrop_if_present(page)
            try:
                _select_month_dropdown(page, month)
            except PlaywrightTimeoutError as e:
                raise BaeminStatsScrapeError(f"{month} 매출 조회 중 월 선택에 실패했습니다: {e}") from e
            page.wait_for_timeout(2_000)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_sales_endpoint"]:
        raise BaeminStatsScrapeError("매출 통계 API 응답을 한 번도 확인하지 못했습니다")
    if not state["observed_crm_endpoint"]:
        raise BaeminStatsScrapeError("재주문율(crmInfo) API 응답을 한 번도 확인하지 못했습니다")

    return sales_responses, crm_responses


def _open_date_range_picker(page) -> None:
    """정산내역 화면의 "날짜 직접 선택" 버튼을 눌러 캘린더를 연다.
    Step 1 조사 결과로 구현을 채운다."""
    raise NotImplementedError("Step 1 조사 결과로 구현")


def _set_date_range(page, start_date: str, end_date: str) -> None:
    """열린 캘린더에서 `start_date`~`end_date`("YYYY-MM-DD")를 지정하고
    적용한다. Step 1 조사 결과로 구현을 채운다."""
    raise NotImplementedError("Step 1 조사 결과로 구현")


def fetch_account_settlement(page, start_date: str, end_date: str) -> list[dict]:
    """정산내역 화면(`/orders/billing`)에서 계정 전체 입금 배치
    (settle/history/summary) organic 응답을 가로챈다. 날짜 범위를 지정한 뒤
    페이지네이션 컨트롤이 있으면 끝까지 반복한다(Step 1 조사 결과에 따라
    "더보기" 클릭 또는 스크롤로 구현 — 리뷰 리스트의 `_MAX_LOAD_MORE_CLICKS`
    패턴을 참고할 수 있다)."""
    responses: list[dict] = []
    observed = {"any": False}

    def _on_response(response) -> None:
        url = response.url
        if urlparse(url).path != "/v3/settle/history/summary":
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
            page.goto("https://self.baemin.com/orders/billing")
        except Exception as e:
            raise BaeminStatsScrapeError(f"정산내역 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(2_000)
        _dismiss_backdrop_if_present(page)
        _open_date_range_picker(page)
        _set_date_range(page, start_date, end_date)
        page.wait_for_timeout(2_000)
        # 페이지네이션 컨트롤이 있다면 Step 1 조사 결과로 여기에 반복 로직을 추가한다.
    finally:
        page.remove_listener("response", _on_response)

    if not observed["any"]:
        raise BaeminStatsScrapeError("정산내역 API 응답을 한 번도 확인하지 못했습니다")

    return responses
```

- [ ] **Step 3: 실제 계정으로 동작 검증**

이 태스크의 Global Constraints에 따라 화면 상호작용 자체는 자동화된 pytest로 덮지 않는다 — 실 계정으로 수동 검증한다. `backend/scrapers/baemin_auth.py`의 `login()`을 그대로 활용한다:

```bash
cd backend
BAEMIN_TEST_ID="<실제 배민 ID>" BAEMIN_TEST_PW="<실제 배민 비밀번호>" .venv/bin/python -c "
import os
from datetime import date, timedelta
from scrapers.baemin_auth import login
from scrapers.baemin_stats import fetch_shop_stats, fetch_account_settlement, recent_months

session = login(os.environ['BAEMIN_TEST_ID'], os.environ['BAEMIN_TEST_PW'])
shop_no = session.shops[0][0]
sales, crm = fetch_shop_stats(session.page, shop_no, recent_months(3))
print('매출 응답 개수:', len(sales), '/ crmInfo 응답 개수:', len(crm))

today = date.today()
deposits = fetch_account_settlement(session.page, (today - timedelta(days=90)).isoformat(), today.isoformat())
print('정산 응답 페이지 수:', len(deposits))
session.close()
"
```
Expected: `매출 응답 개수: 3`(months 3개), `crmInfo 응답 개수: 1`, `정산 응답 페이지 수`는 1 이상. 실패하면 Step 1에서 조사한 선택자를 다시 점검한다.

- [ ] **Step 4: 전체 백엔드 테스트 재확인 (회귀 없음 확인용 — 이 태스크는 새 자동 테스트를 추가하지 않는다)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/scrapers/baemin_stats.py
git commit -m "feat: 가게통계/정산내역 화면의 organic 응답을 가로채는 fetch 함수 추가"
```

---

### Task 3: `review_sync.py` 확장 — 매출/입금/재주문율 upsert + 부분 실패 처리

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 1의 `map_sales_by_date`/`map_deposits_by_date`/`map_repurchase_by_date`/`compute_repurchase_rates`. Task 2의 `fetch_shop_stats`/`fetch_account_settlement`/`BaeminStatsScrapeError`/`recent_months`.
- Produces: `upsert_daily_settlement(db: Session, store_id: int, platform_id: int, settle_date: str, *, sales_amount: int | None = None, deposit_amount: int | None = None) -> None`, `upsert_repurchase_metric(db: Session, store_id: int, platform_id: int, metric_date: str, new_orders: int, repeat_orders: int, rate_raw: float, rate_adjusted: float) -> None`. `_run_sync`가 내부적으로 이 둘을 호출한다 — 다른 태스크가 소비하지는 않는다(엔드포인트/프론트는 그대로).

- [ ] **Step 1: `upsert_daily_settlement`/`upsert_repurchase_metric`에 대한 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 상단 import에 추가(기존 import 유지):

```python
from app.models import DailySettlement, RepurchaseMetric
from app.review_sync import upsert_daily_settlement, upsert_repurchase_metric
```

파일 끝에 추가:

```python
def test_upsert_daily_settlement_creates_new_row(db_session, seeded_user, platforms):
    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).one()
    assert row.sales_amount == 50000
    assert row.deposit_amount == 30000


def test_upsert_daily_settlement_updates_existing_mock_row_for_same_platform(db_session, seeded_user, platforms):
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        settle_date="2026-08-10", sales_amount=999, deposit_amount=888,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    rows = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).all()
    assert len(rows) == 1  # 중복 행이 아니라 갱신
    assert rows[0].sales_amount == 50000
    assert rows[0].deposit_amount == 30000


def test_upsert_daily_settlement_leaves_other_platform_rows_untouched(db_session, seeded_user, platforms):
    # 요기요 Mock 행은 배민 동기화와 무관하게 그대로 남아야 한다.
    yogiyo_row = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id,
        settle_date="2026-08-10", sales_amount=12345, deposit_amount=11111,
    )
    db_session.add(yogiyo_row)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000, deposit_amount=30000,
    )
    db_session.commit()

    untouched = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["yogiyo"].id, settle_date="2026-08-10",
    ).one()
    assert untouched.sales_amount == 12345
    assert untouched.deposit_amount == 11111


def test_upsert_daily_settlement_only_sales_leaves_deposit_untouched_on_existing_row(db_session, seeded_user, platforms):
    # 매출만 갱신하고 입금은 건드리지 않아야 하는 경우(예: 정산 API가 실패해도
    # 매출은 저장 가능해야 하는 부분 성공 시나리오)를 대비한다.
    existing = DailySettlement(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        settle_date="2026-08-10", sales_amount=999, deposit_amount=777,
    )
    db_session.add(existing)
    db_session.commit()

    upsert_daily_settlement(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        sales_amount=50000,
    )
    db_session.commit()

    row = db_session.query(DailySettlement).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, settle_date="2026-08-10",
    ).one()
    assert row.sales_amount == 50000
    assert row.deposit_amount == 777  # 안 건드림


def test_upsert_repurchase_metric_creates_new_row(db_session, seeded_user, platforms):
    upsert_repurchase_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        new_orders=3, repeat_orders=2, rate_raw=0.4, rate_adjusted=0.35,
    )
    db_session.commit()

    row = db_session.query(RepurchaseMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, metric_date="2026-08-10",
    ).one()
    assert row.new_orders == 3
    assert row.repeat_orders == 2
    assert float(row.rate_raw) == 0.4
    assert float(row.rate_adjusted) == 0.35


def test_upsert_repurchase_metric_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    existing = RepurchaseMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, metric_date="2026-08-10",
        new_orders=1, repeat_orders=1, rate_raw="0.5", rate_adjusted="0.5",
    )
    db_session.add(existing)
    db_session.commit()

    upsert_repurchase_metric(
        db_session, seeded_user["store"].id, platforms["baemin"].id, "2026-08-10",
        new_orders=3, repeat_orders=2, rate_raw=0.4, rate_adjusted=0.35,
    )
    db_session.commit()

    rows = db_session.query(RepurchaseMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, metric_date="2026-08-10",
    ).all()
    assert len(rows) == 1
    assert rows[0].new_orders == 3
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_daily_settlement or upsert_repurchase_metric`
Expected: FAIL — `ImportError: cannot import name 'upsert_daily_settlement'`

- [ ] **Step 3: `backend/app/review_sync.py`에 upsert 함수 추가**

파일 상단 import 블록을 다음으로 교체(기존 항목 유지 + 신규 항목 추가):

```python
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_crypto import CredentialCryptoError, decrypt_credential
from app.db import SessionLocal
from app.models import (
    BaeminShopBrand,
    DailySettlement,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
from scrapers.baemin_auth import BaeminLoginError, login as baemin_login
from scrapers.baemin_reviews import BaeminScrapeError, extract_owner_reply, fetch_all_reviews, map_review
from scrapers.baemin_stats import (
    BaeminStatsScrapeError,
    compute_repurchase_rates,
    fetch_account_settlement,
    fetch_shop_stats,
    map_deposits_by_date,
    map_repurchase_by_date,
    map_sales_by_date,
    recent_months,
)
```

`upsert_shop_brand` 함수 다음, `sync_reviews_for_job` 함수 앞에 삽입:

```python
def upsert_daily_settlement(
    db: Session, store_id: int, platform_id: int, settle_date: str,
    *, sales_amount: int | None = None, deposit_amount: int | None = None,
) -> None:
    """`(store_id, platform_id, settle_date)` 기준 upsert. sales_amount와
    deposit_amount는 각각 None이면 기존 값을 건드리지 않는다 — 매출 API는
    성공했는데 정산 API만 실패한 부분 성공 시나리오를 지원하기 위해서다.
    기존 Mock 시드 행이 있으면 갱신하고, 다른 플랫폼(요기요/쿠팡이츠) 행은
    이 함수가 절대 건드리지 않는다(platform_id로 이미 스코프됨)."""
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
        ))
        return
    if sales_amount is not None:
        existing.sales_amount = sales_amount
    if deposit_amount is not None:
        existing.deposit_amount = deposit_amount


def upsert_repurchase_metric(
    db: Session, store_id: int, platform_id: int, metric_date: str,
    new_orders: int, repeat_orders: int, rate_raw: float, rate_adjusted: float,
) -> None:
    """`(store_id, platform_id, metric_date)` 기준 upsert. Task 1의
    `compute_repurchase_rates` 반환값을 그대로 이 함수에 넘기는 용도다."""
    d = date.fromisoformat(metric_date)
    existing = db.scalar(
        select(RepurchaseMetric).where(
            RepurchaseMetric.store_id == store_id,
            RepurchaseMetric.platform_id == platform_id,
            RepurchaseMetric.metric_date == d,
        )
    )
    if existing is None:
        db.add(RepurchaseMetric(
            store_id=store_id, platform_id=platform_id, metric_date=d,
            new_orders=new_orders, repeat_orders=repeat_orders,
            rate_raw=rate_raw, rate_adjusted=rate_adjusted,
        ))
        return
    existing.new_orders = new_orders
    existing.repeat_orders = repeat_orders
    existing.rate_raw = rate_raw
    existing.rate_adjusted = rate_adjusted
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_daily_settlement or upsert_repurchase_metric`
Expected: 7개 테스트 전부 PASS

- [ ] **Step 5: `_run_sync`에 매출/입금/재주문율 동기화를 통합하는 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 상단 import에 다음 한 줄을 추가(기존 import 유지 — 아래 테스트들이 `BaeminStatsScrapeError`를 직접 raise한다):

```python
from scrapers.baemin_stats import BaeminStatsScrapeError
```

파일 끝에 추가:

```python
_SALES_RESP = {"graph": {"data": [{"x": "2026-08-10", "y": 50000.0}]}, "orderAmount": 50000.0, "orderCount": 2}
_CRM_RESP = {
    "orderSummary": {"orderCount": 2, "orderPrice": 50000.0},
    "newReorderSummary": {
        "newOrderCount": 1, "reorderOrderCount": 1,
        "timeNewGraph": {"data": [{"x": "2026-08-10", "y": 1}]},
        "timeReorderGraph": {"data": [{"x": "2026-08-10", "y": 1}]},
    },
}
_SETTLE_RESP = {
    "contents": [{"depositDueDate": "2026-08-10", "giveAmount": 40000, "giveStatus": "REQUEST"}],
    "totalSize": 1,
}


def test_sync_upserts_sales_deposit_repurchase_when_all_succeed(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )
    monkeypatch.setattr(
        review_sync_mod, "fetch_account_settlement",
        lambda page, start_date, end_date: [_SETTLE_RESP],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000
    assert settlement.deposit_amount == 40000

    repurchase = db_session.query(RepurchaseMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, metric_date="2026-08-10",
    ).one()
    assert repurchase.new_orders == 1
    assert repurchase.repeat_orders == 1


def test_sync_sums_stats_across_multiple_shops(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()  # shops = [(11111, "브랜드A"), (22222, "브랜드B")]
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    sales_a = {"graph": {"data": [{"x": "2026-08-10", "y": 30000.0}]}, "orderAmount": 30000.0, "orderCount": 1}
    sales_b = {"graph": {"data": [{"x": "2026-08-10", "y": 20000.0}]}, "orderAmount": 20000.0, "orderCount": 1}

    def _fetch_stats(page, shop_no, months):
        return ([sales_a], [_CRM_RESP]) if shop_no == 11111 else ([sales_b], [_CRM_RESP])

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [_SETTLE_RESP])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000  # 30000 + 20000


def test_sync_reports_success_with_error_message_when_stats_fail_but_reviews_succeed(db_session, sync_setup, monkeypatch):
    """리뷰는 성공했는데 매출/재주문율/입금 수집이 전부 실패해도 job 자체는
    success로 남고 error_message에 어떤 부분이 실패했는지 남아야 한다(설계
    문서 에러 처리 표 참고)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [_RAW_1])

    def _raise_stats(page, shop_no, months):
        raise BaeminStatsScrapeError("매출 통계 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _raise_stats)

    def _raise_settlement(page, start_date, end_date):
        raise BaeminStatsScrapeError("정산내역 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _raise_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"  # 리뷰는 성공했으므로 전체 실패 아님
    assert job.reviews_inserted == 1
    assert "매출" in job.error_message or "정산" in job.error_message
    assert db_session.query(DailySettlement).count() == 0  # 저장된 게 없어야 함


def test_sync_isolates_settlement_failure_from_stats_success(db_session, sync_setup, monkeypatch):
    """매출/재주문율은 성공했는데 입금(정산내역)만 실패해도 매출/재주문율은
    정상 저장돼야 한다 — 항목별 독립 실패 격리."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_shop_stats",
        lambda page, shop_no, months: ([_SALES_RESP], [_CRM_RESP]),
    )

    def _raise_settlement(page, start_date, end_date):
        raise BaeminStatsScrapeError("정산내역 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _raise_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000
    assert settlement.deposit_amount == 0  # 정산 실패라 갱신 안 됨(신규 행 기본값)
    assert "정산" in job.error_message


def test_sync_isolates_one_shop_stats_failure_from_other_shops(db_session, sync_setup, monkeypatch):
    """4개 브랜드 중 한 브랜드의 매출/재주문율 조회만 실패해도 나머지
    브랜드분은 정상 합산돼야 한다(리뷰의 매장별 실패 격리와 동일 원칙)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    def _fetch_stats(page, shop_no, months):
        if shop_no == 11111:
            raise BaeminStatsScrapeError("일시적 오류")
        return [_SALES_RESP], [_CRM_RESP]

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_stats)
    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", lambda page, start_date, end_date: [_SETTLE_RESP])

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    settlement = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-10",
    ).one()
    assert settlement.sales_amount == 50000  # 22222분만 반영, 11111은 실패라 제외
    assert "브랜드A" in job.error_message or "11111" in job.error_message
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k sync_upserts_sales or sync_sums_stats or stats_fail or isolates`
Expected: FAIL — `fetch_shop_stats`/`fetch_account_settlement`가 아직 `review_sync` 모듈 네임스페이스에 없어 `monkeypatch.setattr`가 `AttributeError`를 던진다.

- [ ] **Step 7: `_run_sync`에 매출/입금/재주문율 동기화 통합**

`backend/app/review_sync.py`의 `_run_sync` 함수를 다음으로 교체(기존 리뷰 동기화 for 루프는 그대로 두고, 그 뒤에 새 블록을 추가):

```python
def _run_sync(job: ReviewSyncJob, conn: StorePlatformConnection, db: Session) -> None:
    try:
        credential = decrypt_credential(conn.credential_ciphertext)
        session = baemin_login(credential["login_id"], credential["password"])
    except (BaeminLoginError, CredentialCryptoError) as e:
        job.status = "failed"
        job.error_message = str(e)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    existing_ids = set(db.scalars(
        select(Review.external_review_id).where(Review.external_review_id.isnot(None))
    ).all())

    total_fetched = 0
    total_inserted = 0
    succeeded_any = False
    failed_shops: list[str] = []

    try:
        for shop_no, shop_name in session.shops:
            upsert_shop_brand(db, conn.id, shop_no, shop_name)

            try:
                raw_reviews = fetch_all_reviews(session.page, shop_no)
                mapped_with_raw = [
                    (
                        raw,
                        map_review(
                            raw, store_id=job.store_id, platform_id=job.platform_id,
                            platform_shop_no=str(shop_no),
                        ),
                    )
                    for raw in raw_reviews
                    if raw.get("displayStatus", "DISPLAY") == "DISPLAY"
                ]
            except (BaeminScrapeError, KeyError) as e:
                failed_shops.append(f"{shop_name}: {e}")
                continue

            succeeded_any = True
            total_fetched += len(mapped_with_raw)
            for raw, m in mapped_with_raw:
                if m["external_review_id"] in existing_ids:
                    continue
                review = Review(**m)
                db.add(review)
                db.flush()
                owner_reply = extract_owner_reply(raw)
                if owner_reply is not None:
                    reply_content, replied_at = owner_reply
                    db.add(ReviewReply(
                        review_id=review.id, reply_type="final", style_id=None,
                        content=reply_content, created_at=replied_at,
                    ))
                existing_ids.add(m["external_review_id"])
                total_inserted += 1

        # 리뷰 동기화 성공 여부와 무관하게 매출/재주문율/입금은 별도로
        # 시도한다 — 리뷰가 전부 실패해도(예: 매장 목록이 비정상) 매출은
        # 여전히 유효할 수 있고, 반대로 리뷰만 성공하고 이쪽이 실패해도
        # job 전체를 실패로 만들지 않는다(설계 문서 에러 처리 표).
        # stats_errors는 매장별 실패만 기록한다 — sales_responses/crm_responses가
        # 비어 있는지는 아래에서 각각 따로 확인하므로 여기서 별도로 재해석하지
        # 않는다(하나의 원인을 두 곳에서 서로 다르게 판단하면 불일치가 생긴다).
        stats_errors: list[str] = []
        months = recent_months(3)
        sales_responses: list[dict] = []
        crm_responses: list[dict] = []
        for shop_no, shop_name in session.shops:
            try:
                s, c = fetch_shop_stats(session.page, shop_no, months)
                sales_responses.extend(s)
                crm_responses.extend(c)
            except (BaeminStatsScrapeError, KeyError) as e:
                stats_errors.append(f"{shop_name}: {e}")

        if sales_responses:
            for settle_date, amount in map_sales_by_date(sales_responses).items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, sales_amount=amount,
                )

        if crm_responses:
            rates = compute_repurchase_rates(map_repurchase_by_date(crm_responses))
            for metric_date, r in rates.items():
                upsert_repurchase_metric(
                    db, job.store_id, job.platform_id, metric_date,
                    new_orders=r["new_orders"], repeat_orders=r["repeat_orders"],
                    rate_raw=r["rate_raw"], rate_adjusted=r["rate_adjusted"],
                )

        today = date.today()
        try:
            settlement_responses = fetch_account_settlement(
                session.page, (today - timedelta(days=90)).isoformat(), today.isoformat(),
            )
            for settle_date, amount in map_deposits_by_date(settlement_responses).items():
                upsert_daily_settlement(
                    db, job.store_id, job.platform_id, settle_date, deposit_amount=amount,
                )
        except (BaeminStatsScrapeError, KeyError) as e:
            stats_errors.append(f"정산(입금) 동기화 실패: {e}")
    finally:
        session.close()

    if not succeeded_any:
        job.status = "failed"
        job.error_message = "; ".join(failed_shops) if failed_shops else "동기화할 매장을 찾지 못했습니다"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = "success"
    job.reviews_fetched = total_fetched
    job.reviews_inserted = total_inserted
    messages = []
    if failed_shops:
        messages.append(
            f"{len(session.shops)}개 중 {len(failed_shops)}개 매장 리뷰 동기화 실패: {'; '.join(failed_shops)}"
        )
    if stats_errors:
        messages.append(f"매출/재주문율/입금 동기화 실패: {'; '.join(stats_errors)}")
    if messages:
        job.error_message = " / ".join(messages)
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v`
Expected: 전체 PASS (기존 리뷰 동기화 테스트 포함 회귀 없음)

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 리뷰 동기화에 매출/입금/재주문율 실데이터 upsert 통합"
```

---

### Task 4: 프론트엔드 — "리뷰 동기화" 버튼을 "데이터 동기화"로 변경

**Files:**
- Modify: `frontend/src/app/(app)/account/stores/page.tsx:195`

**Interfaces:**
- Consumes: 없음(기존 `POST /store-connections/baemin/sync-reviews`, `GET /store-connections/baemin/sync-status/{job_id}` 엔드포인트를 그대로 쓴다 — API 응답 형태 변경 없음).
- Produces: 없음(이 태스크가 이 플랜의 마지막 코드 변경이다).

- [ ] **Step 1: 버튼 라벨 변경**

`frontend/src/app/(app)/account/stores/page.tsx:195`의

```tsx
{syncingId === c.id ? "동기화 중..." : "리뷰 동기화"}
```

를 다음으로 교체:

```tsx
{syncingId === c.id ? "동기화 중..." : "데이터 동기화"}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add "frontend/src/app/(app)/account/stores/page.tsx"
git commit -m "feat: 가게 연결 화면 동기화 버튼을 '데이터 동기화'로 이름 변경"
```

---

### Task 5: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 없음.
- Produces: 없음(문서 전용 태스크).

- [ ] **Step 1: "배민 리뷰 연동 (예외 허용)" 절 바로 뒤에 새 절 추가**

`CLAUDE.md`에서 "### 배민 리뷰 연동 (예외 허용)" 절의 끝(다음 `###` 헤더 直前, 현재는 "### 모바일 앱 (예외 허용)" 절 바로 앞)에 아래 절을 삽입한다:

```markdown
### 배민 매출·입금·재주문율 연동 (예외 허용)
원래 "배민의 주문/정산 실데이터 연동은 여전히 범위 밖"이었으나, 리뷰 연동에
이어 실 SaaS 전환 로드맵 3번의 다음 단계로 대시보드의 매출/입금/재주문율도
실제 배민 데이터로 교체하기로 결정했다(2026-08-11). 사장님광장의 "가게통계"
화면(`GET /v3/statistics/orders/summary`로 매장별 일별 매출, `GET
/v3/dashboard/crmInfo`로 매장별 일별 신규/재주문 건수)과 "정산내역" 화면
(`GET /v3/settle/history/summary`)의 organic 응답을 리뷰와 동일한 방식으로
가로챈다. 조사 결과 입금은 매장(브랜드)별 필터가 API에 없어 계정(사업자)
전체 합산으로만 나오는 것을 확인했고, 그래서 매출·재주문율도 브랜드별로
나누지 않고 계정 전체 합산 하나로만 저장하기로 결정했다 — `daily_settlements`/
`repurchase_metrics` 스키마 변경 없이 기존 구조를 그대로 upsert 대상으로
쓴다. 매출/입금은 이번 달 포함 최근 3개월을 백필하고, 재주문율은 배민
API 자체가 고정 최근 7일 창만 줘서 소급이 안 되며 동기화할 때마다 최근
7일 스냅샷만 갱신된다. "가게 연결" 화면의 버튼은 "리뷰 동기화"에서 "데이터
동기화"로 이름을 바꿨다 — 같은 로그인 세션 안에서 리뷰와 매출/입금/재주문율을
한 번에 가져온다. 쿠팡이츠/요기요는 아직 미승인이라 "절대 금지" 그대로
유지. 설계 상세는
`docs/superpowers/specs/2026-08-11-baemin-sales-deposit-repurchase-design.md`
참고.
```

- [ ] **Step 2: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: 배민 매출·입금·재주문율 실데이터 연동 CLAUDE.md 반영"
```
