# 배민 주문내역(개별 주문) 실데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "주문내역" 화면(`/orders`)이 `seed.sql`의 Mock 500건 대신 배민 실제 개별 주문(메뉴/유형/금액/시각)을 보여주도록 만든다.

**Architecture:** 이미 스크래핑 중인 `GET /v4/orders`(현재는 날짜별 매출 합산에만 쓰고 개별 주문은 버림) 응답을 `orders` 테이블에 실제로 저장한다. 매번 넓은 기간을 다시 긁으면 페이지네이션 비용이 커서, `MAX(ordered_at)` 기반 증분 동기화로 바꾼다 — 최초엔 3개월 백필, 이후엔 마지막 저장 시각 근처부터만 다시 조회한다. 스키마 변경 없음.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API), Next.js App Router.

## Global Constraints

- 스키마 변경 없음 — 기존 `orders` 테이블(`order_no` VARCHAR(30) UNIQUE, `order_type` CHECK IN ('delivery','takeout'))을 그대로 쓴다.
- `deliveryType` 매핑은 실측 확인된 두 값만 다룬다: `"DELIVERY"` → `"delivery"`, `"TAKEOUT"` → `"takeout"`. 그 외 값이 나오면 그 주문 하나만 건너뛴다(하드 에러로 전체 동기화를 막지 않는다).
- `menu_summary`는 200자 초과 시 잘라서 저장한다(하드 에러 아님).
- 증분 범위: `orders`에 이 매장·배민의 기존 행이 없으면(`MAX(ordered_at)` 없음) 이번 달 포함 최근 3개월. 있으면 `MAX(ordered_at) − 2일`부터 오늘까지만.
- 요기요/쿠팡이츠는 계속 Mock — 프론트 `주문내역` 화면만 배민으로 필터링해서 요청한다(`orders` 테이블 자체는 안 건드림, `GET /orders`는 이미 `platform_id` 필터를 지원하므로 백엔드 변경 불필요).
- 새 엔드포인트를 만들지 않는다 — 기존 "데이터 동기화"(`_run_sync`)에 통합한다.
- `fetch_orders`(Playwright 화면 조작)는 자동화된 pytest로 덮지 않는다(이 저장소 컨벤션) — 기존 `fetch_current_month_orders`의 시그니처만 바꾸는 것이라 스크래핑 로직 자체는 이미 안정적으로 검증돼 있다. 순수 매핑/범위계산 함수와 upsert/오케스트레이션 로직만 pytest로 촘촘히 테스트한다.
- 배포(및 이번 로컬 검증)에서 기존 Mock 배민 주문 500건은 코드가 아니라 수동 SQL(`DELETE FROM orders WHERE platform_id = <배민 platform_id>`)로 한 번만 정리한다 — 매 동기화마다 지우는 로직을 넣지 않는다(증분 로직과 충돌하기 때문).
- 참고 스펙: `docs/superpowers/specs/2026-08-13-baemin-order-history-design.md`

---

### Task 1: 순수 함수 — 주문 매핑 + 증분 범위 계산

**Files:**
- Modify: `backend/scrapers/baemin_stats.py`
- Test: `backend/tests/test_baemin_stats.py`

**Interfaces:**
- Consumes: 없음(외부 의존 없는 순수 함수).
- Produces: `map_order_rows(order_contents: list[dict]) -> list[dict]`(반환 항목 키: `order_no`, `ordered_at`(문자열, ISO 8601 그대로), `menu_summary`, `order_type`, `amount`). `compute_order_sync_range(latest_ordered_at: datetime | None, today: date) -> tuple[date, date]`. Task 4가 두 함수를 그대로 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_baemin_stats.py` 파일 끝에 추가:

```python
from datetime import date, datetime, timedelta

from scrapers.baemin_stats import compute_order_sync_range, map_order_rows

# 실 계정(2026-08-13 조사)에서 확인한 실제 GET /v4/orders
# contents[].order 필드 형태를 축약한 것. deliveryType은 실측 227건 중
# DELIVERY 223건, TAKEOUT 4건만 나왔다.
_ORDER_DELIVERY = {
    "order": {
        "orderNumber": "T2FE000020VQ",
        "orderDateTime": "2026-08-13T02:19:37",
        "payAmount": 15900,
        "itemsSummary": "[양념조절가능]숯불양념바베큐치킨",
        "deliveryType": "DELIVERY",
    },
}
_ORDER_TAKEOUT = {
    "order": {
        "orderNumber": "B2FD00HZNU",
        "orderDateTime": "2026-08-10T18:02:11",
        "payAmount": 21000,
        "itemsSummary": "[갓성비]1인 숯불양념치밥 SET",
        "deliveryType": "TAKEOUT",
    },
}
_ORDER_UNKNOWN_TYPE = {
    "order": {
        "orderNumber": "X9999999999",
        "orderDateTime": "2026-08-11T09:00:00",
        "payAmount": 9900,
        "itemsSummary": "알 수 없는 유형",
        "deliveryType": "SOMETHING_NEW",
    },
}


def test_map_order_rows_maps_delivery_type():
    result = map_order_rows([_ORDER_DELIVERY])
    assert result == [{
        "order_no": "T2FE000020VQ",
        "ordered_at": "2026-08-13T02:19:37",
        "menu_summary": "[양념조절가능]숯불양념바베큐치킨",
        "order_type": "delivery",
        "amount": 15900,
    }]


def test_map_order_rows_maps_takeout_type():
    result = map_order_rows([_ORDER_TAKEOUT])
    assert result[0]["order_type"] == "takeout"


def test_map_order_rows_skips_unknown_delivery_type():
    # 알려지지 않은 deliveryType은 그 주문만 건너뛴다 — 하드 에러 아님.
    result = map_order_rows([_ORDER_DELIVERY, _ORDER_UNKNOWN_TYPE, _ORDER_TAKEOUT])
    assert len(result) == 2
    assert {r["order_no"] for r in result} == {"T2FE000020VQ", "B2FD00HZNU"}


def test_map_order_rows_truncates_long_menu_summary():
    long_item = {
        "order": {
            "orderNumber": "T1234567890",
            "orderDateTime": "2026-08-13T10:00:00",
            "payAmount": 10000,
            "itemsSummary": "메" * 250,
            "deliveryType": "DELIVERY",
        },
    }
    result = map_order_rows([long_item])
    assert len(result[0]["menu_summary"]) == 200


def test_map_order_rows_empty_list_returns_empty_list():
    assert map_order_rows([]) == []


def test_compute_order_sync_range_no_existing_data_backfills_three_months():
    today = date(2026, 8, 13)
    start, end = compute_order_sync_range(None, today)
    assert end == today
    assert start == date(2026, 5, 13)  # 오늘 포함 최근 3개월


def test_compute_order_sync_range_with_existing_data_uses_two_day_buffer():
    today = date(2026, 8, 13)
    latest = datetime(2026, 8, 10, 15, 30, 0)
    start, end = compute_order_sync_range(latest, today)
    assert end == today
    assert start == date(2026, 8, 8)  # 8/10 - 2일
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v -k "map_order_rows or compute_order_sync_range"`
Expected: FAIL — `ImportError: cannot import name 'map_order_rows' from 'scrapers.baemin_stats'`

- [ ] **Step 3: `backend/scrapers/baemin_stats.py`에 두 함수 추가**

파일 상단 import에 `dateutil` 등 새 의존성은 필요 없다 — 이미 `from datetime import date, timedelta`가 있다(파일 최상단 확인). `datetime`은 아직 import 안 돼 있으므로 추가한다:

```python
from datetime import date, datetime, timedelta
```//기존 `from datetime import date, timedelta`를 이 줄로 교체

`map_orders_to_daily_sales` 함수 다음, `map_deposits_by_date` 함수 앞에 삽입:

```python
_ORDER_TYPE_MAP = {"DELIVERY": "delivery", "TAKEOUT": "takeout"}


def map_order_rows(order_contents: list[dict]) -> list[dict]:
    """`GET /v4/orders` 응답의 `contents[].order.{orderNumber, orderDateTime,
    payAmount, itemsSummary, deliveryType}`를 `orders` 테이블 upsert용
    딕셔너리 리스트로 매핑한다. `deliveryType`이 실측으로 확인된 두 값
    (`DELIVERY`/`TAKEOUT`) 중 하나가 아니면 그 주문 하나만 건너뛴다(하드
    에러로 전체 동기화를 막지 않는다 — 새로운 배달 유형이 배민에 추가돼도
    나머지 주문은 계속 저장돼야 한다). `menu_summary`는 `orders.menu_summary
    VARCHAR(200)` 제약에 맞춰 200자로 자른다."""
    rows: list[dict] = []
    for item in order_contents:
        order = item["order"]
        order_type = _ORDER_TYPE_MAP.get(order["deliveryType"])
        if order_type is None:
            continue
        rows.append({
            "order_no": order["orderNumber"],
            "ordered_at": order["orderDateTime"],
            "menu_summary": order["itemsSummary"][:200],
            "order_type": order_type,
            "amount": order["payAmount"],
        })
    return rows


def compute_order_sync_range(latest_ordered_at: datetime | None, today: date) -> tuple[date, date]:
    """증분 동기화 범위를 계산한다. `latest_ordered_at`은 이 매장·배민의
    `orders` 테이블에 이미 저장된 가장 최근 `ordered_at`(없으면 `None`).
    `None`이면 이번 달 포함 최근 3개월(최초 백필, 또는 Mock 정리 직후)을,
    있으면 그 시각에서 이틀 여유를 두고 오늘까지만 반환한다 — 동기화
    시점 이후 주문 상태가 늦게 확정되는 경우를 대비한 여유다(설계 문서
    "스코프 결정 2" 참고). `order_no` 기준 upsert라 겹치는 기간을 다시
    조회해도 중복 저장되지 않는다."""
    if latest_ordered_at is None:
        return today - timedelta(days=90), today
    return latest_ordered_at.date() - timedelta(days=2), today
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_baemin_stats.py -v -k "map_order_rows or compute_order_sync_range"`
Expected: 7개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/scrapers/baemin_stats.py backend/tests/test_baemin_stats.py
git commit -m "feat: 개별 주문 매핑과 증분 동기화 범위를 계산하는 순수 함수 추가"
```

---

### Task 2: `upsert_order` 함수

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: `app.models.Order`(이미 있음).
- Produces: `upsert_order(db: Session, store_id: int, platform_id: int, *, order_no: str, ordered_at: str, menu_summary: str, order_type: str, amount: int) -> None`. Task 4가 그대로 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 끝에 추가:

```python
def test_upsert_order_creates_new_row(db_session, seeded_user, platforms):
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="[양념조절가능]숯불양념바베큐치킨", order_type="delivery", amount=15900,
    )
    db_session.commit()

    row = db_session.query(Order).filter_by(order_no="T2FE000020VQ").one()
    assert row.store_id == seeded_user["store"].id
    assert row.platform_id == platforms["baemin"].id
    assert row.menu_summary == "[양념조절가능]숯불양념바베큐치킨"
    assert row.order_type == "delivery"
    assert row.amount == 15900


def test_upsert_order_updates_existing_row_without_duplicate(db_session, seeded_user, platforms):
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="원래 메뉴", order_type="delivery", amount=15900,
    )
    db_session.commit()

    # 같은 order_no로 다시 upsert(예: 증분 조회의 2일 여유 구간이 겹칠 때)
    upsert_order(
        db_session, seeded_user["store"].id, platforms["baemin"].id,
        order_no="T2FE000020VQ", ordered_at="2026-08-13T02:19:37",
        menu_summary="바뀐 메뉴", order_type="takeout", amount=16900,
    )
    db_session.commit()

    rows = db_session.query(Order).filter_by(order_no="T2FE000020VQ").all()
    assert len(rows) == 1
    assert rows[0].menu_summary == "바뀐 메뉴"
    assert rows[0].order_type == "takeout"
    assert rows[0].amount == 16900
```

`backend/tests/test_review_sync.py` 파일 상단 import 블록에 이미 있는 `from app.models import (...)` 목록에 `Order`를 추가하고, `from app.review_sync import (...)` 목록에 `upsert_order`를 추가한다(기존 항목들은 그대로 유지).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_order`
Expected: FAIL — `ImportError: cannot import name 'upsert_order'`

- [ ] **Step 3: `backend/app/review_sync.py`에 `upsert_order` 추가**

파일 상단 `from app.models import (...)` 목록에 `Order`를 추가(알파벳 순서 유지, 기존 `DailySettlement` 다음 `Order`가 오도록):

```python
from app.models import (
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    Order,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
```

`upsert_brand_ad_click_metric` 함수 다음, `sync_reviews_for_job` 함수 앞에 삽입:

```python
def upsert_order(
    db: Session, store_id: int, platform_id: int,
    *, order_no: str, ordered_at: str, menu_summary: str, order_type: str, amount: int,
) -> None:
    """`order_no` 기준 upsert(`orders.order_no`는 매장과 무관하게 전역
    유일 — schema.sql의 `UNIQUE` 제약과 동일하게 `order_no`만으로 조회한다).
    증분 동기화(`compute_order_sync_range`)가 며칠씩 겹치는 기간을 다시
    조회할 수 있어 같은 `order_no`가 여러 번 들어올 수 있다 — 그때마다
    최신 값으로 덮어쓴다(주문 상태가 뒤늦게 바뀌는 경우를 반영하기 위해)."""
    existing = db.scalar(select(Order).where(Order.order_no == order_no))
    if existing is None:
        db.add(Order(
            store_id=store_id, platform_id=platform_id, order_no=order_no,
            ordered_at=ordered_at, menu_summary=menu_summary,
            order_type=order_type, amount=amount,
        ))
        # 다른 upsert_* 함수들과 같은 이유(autoflush=False인 app.db.SessionLocal)
        # — 증분 조회 범위 안에서 같은 order_no가 여러 번 들어올 수 있어
        # flush 없이는 두 번째부터 select()가 방금 add()한 행을 못 보고
        # 중복 INSERT를 시도해 UniqueViolation이 난다.
        db.flush()
        return
    existing.ordered_at = ordered_at
    existing.menu_summary = menu_summary
    existing.order_type = order_type
    existing.amount = amount
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k upsert_order`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 개별 주문을 order_no 기준으로 upsert하는 함수 추가"
```

---

### Task 3: `fetch_orders` 일반화 (날짜 범위 인자화)

**Files:**
- Modify: `backend/scrapers/baemin_stats.py`
- Modify: `backend/app/review_sync.py`
- Modify: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: 없음(기존 `fetch_current_month_orders`의 스크래핑 로직 자체는 변경 없음).
- Produces: `fetch_orders(page, start_date: str, end_date: str) -> list[dict]`(기존 `fetch_current_month_orders(page)`를 대체 — 함수명과 시그니처만 바뀐다). Task 4가 증분 범위와 함께 이 함수를 호출한다.

이 태스크는 스크래핑 로직 자체를 바꾸지 않는다 — 함수 내부에서 하드코딩하던 `today = date.today(); start_date = today.replace(day=1).isoformat(); end_date = today.isoformat()` 세 줄을 지우고, 그 자리에 쓰이던 `start_date`/`end_date` 지역변수를 함수 인자로 받는 것으로 바꿀 뿐이다.

- [ ] **Step 1: `backend/scrapers/baemin_stats.py` — 함수 시그니처 변경**

`fetch_current_month_orders` 함수 정의:

```python
def fetch_current_month_orders(page) -> list[dict]:
    """주문내역 화면(`/orders/history`)에서 이번 달 1일부터 오늘까지의 주문
```

를 다음으로 교체(문서화 내용은 아래 Step 2에서 마저 정리):

```python
def fetch_orders(page, start_date: str, end_date: str) -> list[dict]:
    """주문내역 화면(`/orders/history`)에서 `start_date`~`end_date`("YYYY-MM-DD")
```

함수 본문 시작 부분:

```python
    today = date.today()
    start_date = today.replace(day=1).isoformat()
    end_date = today.isoformat()

    collected: dict[object, dict] = {}
```

를 다음으로 교체(날짜 계산 세 줄만 삭제, 나머지는 그대로):

```python
    collected: dict[object, dict] = {}
```

- [ ] **Step 2: docstring 갱신**

`fetch_orders` 함수의 나머지 docstring 본문(원래 "가게통계 화면의 월별 조회가 진행 중인 이번 달을 지원하지 않는다는 제약(...)을 보완하기 위한 함수 — 호출자는 이 함수가 반환한 `contents` 항목 리스트를 `map_orders_to_daily_sales`로 집계해 이번 달 매출만 별도로 채운다.")는 그대로 두되, 첫 문장 끝에 이어 아래 문단을 추가한다:

```python
    2026-08-13부터는 두 가지 목적으로 함께 쓰인다: (1) 기존처럼 이번 달
    1일~오늘 범위로 호출해 진행 중인 이번 달 매출을 보완하고(호출자가
    `map_orders_to_daily_sales`로 집계), (2) `compute_order_sync_range`가
    계산한 범위로 호출해 개별 주문 자체를 `orders` 테이블에 저장한다
    (호출자가 `map_order_rows`로 매핑). 날짜 범위만 다를 뿐 스크래핑
    로직은 완전히 동일하다 — 원래 함수 내부에 하드코딩돼 있던 "이번 달
    1일~오늘"을 호출자가 넘기는 인자로 바꿨을 뿐이다.
```

- [ ] **Step 3: `backend/app/review_sync.py` — import와 기존 호출부 갱신**

`from scrapers.baemin_stats import (...)` 목록에서 `fetch_current_month_orders`를 `fetch_orders`로 교체(알파벳 순서 유지):

```python
from scrapers.baemin_stats import (
    BaeminStatsScrapeError,
    compute_repurchase_rates,
    fetch_account_settlement,
    fetch_orders,
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

"이번 달 매출 보완" 블록:

```python
        try:
            current_month_orders = fetch_current_month_orders(session.page)
            current_month_sales = map_orders_to_daily_sales(current_month_orders)
```

를 다음으로 교체:

```python
        try:
            today_for_orders = date.today()
            current_month_orders = fetch_orders(
                session.page, today_for_orders.replace(day=1).isoformat(), today_for_orders.isoformat(),
            )
            current_month_sales = map_orders_to_daily_sales(current_month_orders)
```

- [ ] **Step 4: `backend/tests/test_review_sync.py` — 기존 monkeypatch 전부 갱신**

`fetch_current_month_orders`를 참조하는 곳이 총 6곳이다(`grep -n fetch_current_month_orders backend/tests/test_review_sync.py`로 확인). 전부 다음 두 가지 기계적 규칙으로 바꾼다:

1. 문자열 `"fetch_current_month_orders"` → `"fetch_orders"`
2. 그 대상에 붙는 람다의 시그니처 `lambda page: ...` → `lambda page, start_date, end_date: ...`(본문은 그대로)

예를 들어 `sync_setup` 픽스처 안의 기본값:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_current_month_orders", lambda page: [])
```

는:

```python
    monkeypatch.setattr(review_sync_mod, "fetch_orders", lambda page, start_date, end_date: [])
```

로, 특정 반환값을 주는 곳(예: 630번대 줄):

```python
    monkeypatch.setattr(
        review_sync_mod, "fetch_current_month_orders",
        lambda page: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
```

는:

```python
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date: [{"order": {"orderNumber": "T1", "orderDateTime": "2026-08-15T12:00:00", "payAmount": 12000}}],
    )
```

로, 실패를 발생시키는 헬퍼 함수(예: `_raise_current_month`)는:

```python
    def _raise_current_month(page):
        raise BaeminStatsScrapeError("주문내역 조회 실패")
```

는:

```python
    def _raise_current_month(page, start_date, end_date):
        raise BaeminStatsScrapeError("주문내역 조회 실패")
```

로 바꾼다(함수 이름 자체는 안 바꿔도 된다 — 참조하는 `monkeypatch.setattr` 쪽의 대상 이름만 `fetch_orders`로 바뀌면 된다).

이 6곳을 전부 고친 뒤 `grep -n fetch_current_month_orders backend/tests/test_review_sync.py`를 실행해 결과가 0건인지 확인한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "current_month or merges_current_month"`
Expected: 관련 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 7: 실 계정으로 동작 재검증**

스크래핑 로직 자체는 안 바뀌었지만(날짜 인자만 외부화), 시그니처 변경 과정에서 오타나 인자 순서 실수가 있을 수 있으므로 실 계정으로 짧게 재확인한다:

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
from scrapers.baemin_stats import fetch_orders

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    ciphertext = conn.execute(text(\"select credential_ciphertext from store_platform_connections where id = 6\")).one()[0]
cred = decrypt_credential(ciphertext)

session = login(cred['login_id'], cred['password'])
today = date.today()
orders = fetch_orders(session.page, (today - timedelta(days=3)).isoformat(), today.isoformat())
print('최근 3일 주문 건수:', len(orders))
if orders:
    print('샘플:', orders[0]['order'].get('orderNumber'), orders[0]['order'].get('deliveryType'))
session.close()
"
```
Expected: 에러 없이 실행되고 최근 3일치 주문 건수가 합리적인 숫자로 찍힌다(0건이어도 에러가 아니면 정상 — 최근 3일에 주문이 없었을 수도 있다).

- [ ] **Step 8: 커밋**

```bash
git add backend/scrapers/baemin_stats.py backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "refactor: fetch_current_month_orders를 날짜 범위 인자를 받는 fetch_orders로 일반화"
```

---

### Task 4: `review_sync.py` 통합 — 증분 동기화로 개별 주문 저장

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 1의 `map_order_rows`, `compute_order_sync_range`. Task 2의 `upsert_order`. Task 3의 `fetch_orders`.
- Produces: 없음(내부 오케스트레이션 — 다른 태스크가 직접 소비하지 않는다).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 파일 상단 import에 `map_order_rows`, `compute_order_sync_range`를 `from scrapers.baemin_stats import (...)` 목록에 추가한다(기존 항목 유지).

파일 끝에 추가:

```python
_ORDER_ITEM_A = {
    "order": {
        "orderNumber": "T2FE000020VQ", "orderDateTime": "2026-08-13T02:19:37",
        "payAmount": 15900, "itemsSummary": "숯불양념바베큐치킨", "deliveryType": "DELIVERY",
    },
}
_ORDER_ITEM_B = {
    "order": {
        "orderNumber": "B2FD00HZNU", "orderDateTime": "2026-08-10T18:02:11",
        "payAmount": 21000, "itemsSummary": "1인 숯불양념치밥 SET", "deliveryType": "TAKEOUT",
    },
}


def test_sync_upserts_individual_orders(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_orders",
        lambda page, start_date, end_date: [_ORDER_ITEM_A, _ORDER_ITEM_B],
    )

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    row_a = db_session.query(Order).filter_by(order_no="T2FE000020VQ").one()
    assert row_a.amount == 15900
    assert row_a.order_type == "delivery"
    row_b = db_session.query(Order).filter_by(order_no="B2FD00HZNU").one()
    assert row_b.order_type == "takeout"


def test_sync_uses_incremental_range_when_orders_already_exist(db_session, sync_setup, monkeypatch):
    """이미 저장된 주문이 있으면 compute_order_sync_range가 계산한 좁은
    범위로 fetch_orders를 호출해야 한다 — 3개월 전체를 다시 긁지 않는다."""
    import app.review_sync as review_sync_mod
    from datetime import date, datetime

    job, conn = sync_setup
    db_session.add(Order(
        store_id=job.store_id, platform_id=job.platform_id, order_no="OLD0000001",
        ordered_at=datetime(2026, 8, 10, 9, 0, 0),
        menu_summary="기존 주문", order_type="delivery", amount=10000,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    captured_ranges = []

    def _fake_fetch_orders(page, start_date, end_date):
        captured_ranges.append((start_date, end_date))
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _fake_fetch_orders)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 두 번 호출된다: "이번 달 매출 보완"(이번 달 1일~오늘)과
    # "개별 주문 저장"(증분 범위) — 둘 중 하나는 2026-08-08(8/10 - 2일)로
    # 시작해야 한다.
    assert any(r[0] == "2026-08-08" for r in captured_ranges)


def test_sync_isolates_individual_order_failure_from_current_month_sales(db_session, sync_setup, monkeypatch):
    """개별 주문 저장이 실패해도 이번 달 매출 보완(별도 fetch_orders 호출)은
    영향받지 않아야 한다 — 항목별 독립 실패 격리 원칙."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    call_count = {"n": 0}

    def _flaky_fetch_orders(page, start_date, end_date):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 첫 호출(이번 달 매출 보완)은 성공
            return [_ORDER_ITEM_A]
        # 두 번째 호출(개별 주문 저장)은 실패
        raise BaeminStatsScrapeError("주문내역 상세 조회 실패")

    monkeypatch.setattr(review_sync_mod, "fetch_orders", _flaky_fetch_orders)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    current_month_row = db_session.query(DailySettlement).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, settle_date="2026-08-13",
    ).one()
    assert current_month_row.sales_amount == 15900  # 매출 보완은 정상 반영
    assert db_session.query(Order).filter_by(order_no="T2FE000020VQ").count() == 0  # 개별 주문 저장은 실패
    assert "주문내역" in job.error_message
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "individual_order or incremental_range"`
Expected: FAIL — `_run_sync`가 아직 개별 주문을 저장하지 않으므로 `Order` 행이 안 생겨 `.one()`이 `NoResultFound`를 낸다.

- [ ] **Step 3: `_run_sync`에 개별 주문 저장 블록 추가**

`backend/app/review_sync.py`의 `_run_sync` 함수 안, "이번 달 매출 보완" 블록(Task 3에서 `today_for_orders`를 쓰도록 고친 블록, `except Exception as e: stats_errors.append(f"이번 달 매출(주문내역) 동기화 실패: {e}")`로 끝남) 바로 다음, "재주문율" 블록(`if crm_responses:`로 시작하는 블록) 앞에 삽입:

```python
        try:
            latest_order = db.scalar(
                select(func.max(Order.ordered_at)).where(
                    Order.store_id == job.store_id, Order.platform_id == job.platform_id,
                )
            )
            order_range_start, order_range_end = compute_order_sync_range(latest_order, date.today())
            order_contents = fetch_orders(
                session.page, order_range_start.isoformat(), order_range_end.isoformat(),
            )
            order_rows = map_order_rows(order_contents)
            for row in order_rows:
                upsert_order(
                    db, job.store_id, job.platform_id,
                    order_no=row["order_no"], ordered_at=row["ordered_at"],
                    menu_summary=row["menu_summary"], order_type=row["order_type"], amount=row["amount"],
                )
            if order_rows:
                stats_succeeded_any = True
        except Exception as e:
            stats_errors.append(f"주문내역(개별 주문) 동기화 실패: {e}")
```

파일 상단 import 블록의 `from sqlalchemy import select, update`를 `from sqlalchemy import func, select, update`로 교체(`func.max`를 쓰기 위해).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "individual_order or incremental_range"`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 5: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 6: 로컬 검증 DB의 기존 Mock 배민 주문 정리 (일회성, 수동)**

이 저장소는 마이그레이션 파일을 안 쓰므로, 로컬 검증에서도 실제 배포 때와 동일하게 수동 SQL로 한 번만 정리한다:

```bash
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c "
DELETE FROM orders WHERE platform_id = (SELECT id FROM platforms WHERE code = 'baemin');
"
```

이후 "데이터 동기화"를 실행하면 `MAX(ordered_at)`이 없는 상태로 시작되므로 `compute_order_sync_range`가 자동으로 3개월 백필을 수행한다 — 별도의 "첫 동기화" 플래그가 필요 없다는 설계 문서의 결정을 그대로 확인할 수 있다.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 데이터 동기화에 증분 방식 개별 주문 저장 통합"
```

---

### Task 5: 프론트엔드 — 주문내역 화면 배민 필터링

**Files:**
- Modify: `frontend/src/app/(app)/sales/orders/page.tsx`

**Interfaces:**
- Consumes: `GET /platforms`(이미 있음, 대시보드가 배민 `platform_id`를 찾을 때 쓰는 것과 동일한 패턴), `GET /orders?store_id=&platform_id=`(이미 `platform_id` 필터 지원, 백엔드 변경 없음).
- Produces: 없음(터미널 UI 컴포넌트).

- [ ] **Step 1: 배민 `platform_id` 조회 후 필터링해서 요청**

`frontend/src/app/(app)/sales/orders/page.tsx` 전체를:

```tsx
"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Order = {
  id: number;
  order_no: string;
  platform_name: string;
  platform_color: string | null;
  ordered_at: string;
  menu_summary: string;
  order_type: string;
  amount: number;
};
type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };

export default function OrdersPage() {
  const { storeId } = useStoreContext();
  const [orders, setOrders] = useState<Order[]>([]);
  const [baeminPlatformId, setBaeminPlatformId] = useState<number | null>(null);

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then((rows) => {
      setBaeminPlatformId(rows.find((p) => p.code === "baemin")?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!storeId || !baeminPlatformId) return;
    apiGet<Order[]>(`/orders?store_id=${storeId}&platform_id=${baeminPlatformId}`).then(setOrders);
  }, [storeId, baeminPlatformId]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">주문내역</h1>
        <p className="text-sm text-muted">최근 30일 주문 내역입니다.</p>
      </div>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-xs text-muted">
                <th className="py-2 font-medium">플랫폼</th>
                <th className="font-medium">주문시각</th>
                <th className="font-medium">주문번호</th>
                <th className="font-medium">주문내역</th>
                <th className="font-medium">주문유형</th>
                <th className="text-right font-medium">주문금액</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="border-b border-border-subtle last:border-0">
                  <td className="py-3">
                    <span
                      className="rounded px-2 py-0.5 text-xs font-medium"
                      style={{ backgroundColor: `${o.platform_color}26`, color: o.platform_color ?? undefined }}
                    >
                      {o.platform_name}
                    </span>
                  </td>
                  <td className="text-xs text-muted">{new Date(o.ordered_at).toLocaleString("ko-KR")}</td>
                  <td className="text-xs text-muted">{o.order_no}</td>
                  <td>{o.menu_summary}</td>
                  <td className="text-xs text-muted">{o.order_type === "delivery" ? "배달" : "포장"}</td>
                  <td className="text-right font-medium">{won(o.amount)}</td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr><td colSpan={6} className="py-6 text-center text-muted">주문 내역이 없습니다.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
```

로 교체(변경점: `PlatformOption` 타입 추가, `baeminPlatformId` state와 그걸 채우는 `useEffect` 추가, 주문 조회 `useEffect`가 `baeminPlatformId`를 기다렸다가 쿼리에 `&platform_id=`를 붙임, 부제 문구를 "최근 60일"에서 "최근 30일"로 변경. 나머지 JSX는 기존과 동일).

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 로컬에서 실제 계정으로 확인**

로컬 백엔드(`:8000`)/프론트(`:3000`) 실행 후(Task 4의 Mock 정리 SQL과 "데이터 동기화"가 이미 실행됐다는 전제), 주문내역 화면을 열어 배민 실제 주문만(요기요/쿠팡이츠 없이) 뜨는지, 메뉴명·주문유형(배달/포장)·금액이 배민 사이트에서 직접 확인한 값과 맞는지 확인한다.

- [ ] **Step 4: 커밋**

```bash
git add "frontend/src/app/(app)/sales/orders/page.tsx"
git commit -m "feat: 주문내역 화면이 배민 실제 개별 주문만 보여주도록 필터링"
```

---

## CLAUDE.md 갱신 (마지막 태스크 이후, 최종 리뷰 전)

"배민 정산 상세(수수료/배달비/고객할인/우가클비용) 연동 (예외 허용)" 절
바로 아래에 "배민 주문내역(개별 주문) 연동 (예외 허용)" 절을 추가한다.
`daily_settlements`/`orders` 테이블 설명 중 `orders`가 여전히 Mock으로만
서술돼 있다면 실데이터 반영 사실에 맞게 갱신한다. 별도 태스크로 분리하지
않고 Task 5 완료 후 전체 리뷰 단계에서 함께 처리한다(이전 실데이터 연동
계획들과 동일한 관례).
