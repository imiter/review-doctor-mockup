# 배민 데이터 동기화 증분 조회 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "데이터 동기화" 버튼의 두 번째 이후 실행부터, 리뷰/월별매출/입금/정산상세/우가클 5개 데이터 소스가 이미 가진 데이터를 다시 긁지 않고 새 구간만 조회하게 만든다.

**Architecture:** `orders`(개별 주문내역)가 이미 쓰는 "매번 DB에서 커서를 계산 → 그 범위만 조회" 패턴을 나머지 5개 소스에 동일하게 확장한다. 새 컬럼/테이블은 추가하지 않는다 — 각 소스가 이미 쓰는 테이블에서 `MAX(날짜)`/`EXISTS`로 그때그때 커서를 계산한다. 커서 계산은 순수 함수로 뽑아 pytest로 테스트하고, `_run_sync`(`backend/app/review_sync.py`)가 그 커서를 실제 fetch 호출에 연결한다.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0(ORM, `Mapped[...]`), pytest, Playwright(sync API, 이 플랜에서는 직접 건드리지 않고 fetch 함수 시그니처만 확장).

## Global Constraints

- 새 DB 컬럼/테이블/마이그레이션 없음 — 전부 기존 테이블에서 매번 계산.
- 최초 동기화(커서/기존 행 없음) 동작은 절대 바뀌면 안 된다 — 전체 백필 그대로.
- Playwright를 직접 다루는 코드는 pytest로 커버하지 않는다(기존 관례) — 이 플랜의 신규 테스트는 순수 함수 또는 `_FakePage`/monkeypatch 기반 기존 패턴만 쓴다.
- 리뷰 조기종료 임계값: 연속 5개(상수로 정의, 매직넘버로 흩뿌리지 않음).
- 정산 계열(입금/정산상세) 재조회 버퍼: 커서 − 2일 (orders와 동일).
- 월별 소스(가게통계 매출/우가클)는 "해당 월에 그 소스 전용 컬럼이 채워진 행이 하나라도 있으면" 이미 동기화된 것으로 판단한다. `daily_settlements`는 매출/입금/정산상세 컬럼을 한 행에서 공유하므로 반드시 `sales_amount IS NOT NULL`처럼 컬럼 단위로 판단해야 한다(행 존재만으로 판단하면 안 됨).
- 재주문율(crmInfo)은 날짜 소급이 안 되는 "최근 7일" 고정 스냅샷이라, 매출을 전부 건너뛰어도 매 동기화마다 최소 한 달은 방문해서 갱신해야 한다.
- 우가클의 진행 중인 이번 달은 이미 행이 있어도 항상 재조회 대상에 포함한다.
- 관련 설계 문서: `docs/superpowers/specs/2026-08-19-baemin-sync-incremental-fetch-design.md`

---

## 파일 구조

- **`backend/scrapers/baemin_stats.py`** (수정): `compute_order_sync_range` 바로 뒤에 `compute_settlement_sync_range`(입금/정산상세 공용)와 `filter_months_needing_sync`(매출/우가클 공용)를 추가한다.
- **`backend/scrapers/baemin_reviews.py`** (수정): `fetch_all_reviews`가 `existing_ids`를 받아 연속 known-id 조기종료를 하도록 확장. 조기종료 판단 자체는 `_consecutive_known_count` 순수 함수로 분리.
- **`backend/app/review_sync.py`** (수정): `_run_sync`이 위 세 함수를 실제로 호출해 조회 범위/대상을 좁힌다.
- **`backend/tests/test_baemin_stats.py`** (수정): 새 순수 함수 2개 테스트 추가.
- **`backend/tests/test_baemin_reviews.py`** (수정): 조기종료 테스트 추가.
- **`backend/tests/test_review_sync.py`** (수정): 기존 `fetch_all_reviews` monkeypatch 33곳을 새 시그니처와 호환되게 고치고, 통합 테스트 추가.
- **`CLAUDE.md`** (수정): "배민 매출·입금·재주문율 연동" 절 뒤에 이 변경 요약 추가.

---

### Task 1: `compute_settlement_sync_range` (입금/정산상세 공용 커서 계산)

**Files:**
- Modify: `backend/scrapers/baemin_stats.py` (기존 `compute_order_sync_range` 함수, 약 431~459번째 줄, 바로 뒤에 추가)
- Test: `backend/tests/test_baemin_stats.py`

**Interfaces:**
- Consumes: 없음(순수 함수, 표준 라이브러리 `date`/`timedelta`만 사용 — 이미 파일 상단에 import돼 있음)
- Produces: `compute_settlement_sync_range(latest_settled_date: date | None, today: date, *, backfill_days: int) -> tuple[date, date]` — Task 4가 입금(90일)·정산상세(30일) 양쪽에서 `backfill_days`만 다르게 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_baemin_stats.py`의 import 블록(파일 최상단, `from scrapers.baemin_stats import (...)`)에 `compute_settlement_sync_range`를 추가하고, `compute_order_sync_range` 관련 테스트들(`test_compute_order_sync_range_*`, 약 423~473번째 줄) 바로 뒤에 아래 테스트를 추가한다:

```python
def test_compute_settlement_sync_range_no_cursor_backfills_default_window():
    today = date(2026, 8, 19)
    start, end = compute_settlement_sync_range(None, today, backfill_days=90)
    assert end == today
    assert start == date(2026, 5, 21)  # 90일 전


def test_compute_settlement_sync_range_with_cursor_uses_two_day_buffer():
    today = date(2026, 8, 19)
    latest = date(2026, 8, 15)
    start, end = compute_settlement_sync_range(latest, today, backfill_days=90)
    assert end == today
    assert start == date(2026, 8, 13)  # 8/15 - 2일


def test_compute_settlement_sync_range_respects_different_backfill_days():
    # 정산 상세는 30일 기본폭을 쓴다 — 같은 함수를 backfill_days만 바꿔 재사용.
    today = date(2026, 8, 19)
    start, end = compute_settlement_sync_range(None, today, backfill_days=30)
    assert end == today
    assert start == date(2026, 7, 20)  # 30일 전


def test_compute_settlement_sync_range_cursor_today_does_not_go_past_start():
    # 커서가 오늘이어도 에러 없이 동작해야 한다(퇴화 케이스).
    today = date(2026, 8, 19)
    start, end = compute_settlement_sync_range(today, today, backfill_days=90)
    assert end == today
    assert start == date(2026, 8, 17)  # 오늘 - 2일
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_stats.py -k compute_settlement_sync_range -v`
Expected: FAIL — `ImportError: cannot import name 'compute_settlement_sync_range'`

- [ ] **Step 3: 최소 구현 작성**

`backend/scrapers/baemin_stats.py`의 `compute_order_sync_range` 함수 바로 뒤(약 459번째 줄 다음)에 추가:

```python
def compute_settlement_sync_range(
    latest_settled_date: date | None, today: date, *, backfill_days: int,
) -> tuple[date, date]:
    """정산 계열(입금/정산 상세) 증분 조회 범위를 계산한다.
    `compute_order_sync_range`와 같은 패턴이지만 커서 타입이 다르다 —
    `DailySettlement.settle_date`는 순수 `date` 컬럼이라(orders의
    `ordered_at`과 달리 TIMESTAMPTZ가 아님) 타임존 변환이 필요 없다.

    `latest_settled_date`가 없으면(최초 동기화, 또는 아직 이 소스가 한
    번도 성공한 적 없음) `backfill_days` 전부터 오늘까지 전체를 반환한다
    (입금은 90일, 정산 상세는 30일 — 호출부가 다르게 넘긴다). 있으면 그
    날짜에서 이틀 여유를 두고 오늘까지만 반환한다 — 정산 배치 상태가
    동기화 시점 이후 확정될 수 있어서다(설계 문서 참고, orders와 동일한
    이유)."""
    if latest_settled_date is None:
        return today - timedelta(days=backfill_days), today
    return latest_settled_date - timedelta(days=2), today
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_stats.py -k compute_settlement_sync_range -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
cd backend && git add scrapers/baemin_stats.py tests/test_baemin_stats.py
git commit -m "feat: 정산 계열 증분 조회 범위 계산 함수 추가

orders의 compute_order_sync_range와 동일한 커서-2일 패턴을 입금/정산
상세에도 쓸 수 있도록 backfill_days를 인자로 받는 공용 함수로 만든다."
```

---

### Task 2: `filter_months_needing_sync` (매출/우가클 공용 월 필터)

**Files:**
- Modify: `backend/scrapers/baemin_stats.py` (Task 1에서 추가한 `compute_settlement_sync_range` 바로 뒤)
- Test: `backend/tests/test_baemin_stats.py`

**Interfaces:**
- Consumes: 없음(순수 함수)
- Produces: `filter_months_needing_sync(months: list[str], synced_months: set[str], *, always_include: set[str] | None = None) -> list[str]` — Task 4가 가게통계(매출)·우가클 양쪽에서 쓴다. 입력 순서를 그대로 유지한 부분집합을 반환한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`test_baemin_stats.py`의 import 블록에 `filter_months_needing_sync`를 추가하고, Task 1에서 추가한 테스트들 바로 뒤에:

```python
def test_filter_months_needing_sync_keeps_only_unsynced_months():
    months = ["2026-06", "2026-07", "2026-08"]
    synced = {"2026-06"}
    assert filter_months_needing_sync(months, synced) == ["2026-07", "2026-08"]


def test_filter_months_needing_sync_returns_all_when_nothing_synced():
    months = ["2026-06", "2026-07", "2026-08"]
    assert filter_months_needing_sync(months, set()) == months


def test_filter_months_needing_sync_returns_empty_when_everything_synced():
    months = ["2026-06", "2026-07", "2026-08"]
    synced = {"2026-06", "2026-07", "2026-08"}
    assert filter_months_needing_sync(months, synced) == []


def test_filter_months_needing_sync_always_include_overrides_synced():
    # 우가클의 진행 중인 이번 달은 이미 동기화됐어도 항상 포함해야 한다.
    months = ["2026-06", "2026-07", "2026-08"]
    synced = {"2026-06", "2026-07", "2026-08"}
    result = filter_months_needing_sync(months, synced, always_include={"2026-08"})
    assert result == ["2026-08"]


def test_filter_months_needing_sync_preserves_input_order():
    months = ["2026-06", "2026-07", "2026-08"]
    synced = {"2026-07"}
    assert filter_months_needing_sync(months, synced) == ["2026-06", "2026-08"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_stats.py -k filter_months_needing_sync -v`
Expected: FAIL — `ImportError: cannot import name 'filter_months_needing_sync'`

- [ ] **Step 3: 최소 구현 작성**

```python
def filter_months_needing_sync(
    months: list[str], synced_months: set[str], *, always_include: set[str] | None = None,
) -> list[str]:
    """이미 동기화된 달(`synced_months`)을 `months`에서 제외한다.
    `always_include`에 있는 달은 `synced_months`에 있어도 항상 포함한다
    (예: 우가클의 진행 중인 이번 달 — 완료된 달과 달리 매번 최신 상태로
    갱신돼야 한다). 어떤 컬럼 기준으로 "동기화됨"을 판단할지는 호출부의
    책임이다 — 이 함수는 그 판단 결과(집합)만 받아 필터링만 한다."""
    always_include = always_include or set()
    return [m for m in months if m not in synced_months or m in always_include]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_stats.py -k filter_months_needing_sync -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
cd backend && git add scrapers/baemin_stats.py tests/test_baemin_stats.py
git commit -m "feat: 월별 소스(매출/우가클) 증분 필터 함수 추가"
```

---

### Task 3: 리뷰 페이지네이션 연속 known-ID 조기종료

**Files:**
- Modify: `backend/scrapers/baemin_reviews.py`
- Test: `backend/tests/test_baemin_reviews.py`

**Interfaces:**
- Consumes: 없음(신규 헬퍼는 순수 함수)
- Produces:
  - `fetch_all_reviews(page, shop_no: int, existing_ids: set[int] | None = None) -> list[dict]` — 기존 시그니처에 `existing_ids` 파라미터 추가(기본값 `None` → 빈 집합 취급). Task 4가 `_run_sync`에서 `existing_ids=existing_ids`로 호출한다.
  - `_consecutive_known_count(ids_in_order: list[int], existing_ids: set[int]) -> int` — 내부 헬퍼지만 직접 import해 단위 테스트한다(이 코드베이스의 기존 관례 — `_should_count_sales_response`도 같은 방식으로 직접 테스트됨).

- [ ] **Step 1: `_consecutive_known_count` 실패하는 테스트 작성**

`backend/tests/test_baemin_reviews.py`의 import 블록(`from scrapers.baemin_reviews import (...)`, 파일 최상단)에 `_consecutive_known_count`를 추가하고, `test_map_review_handles_empty_content` 함수(약 116번째 줄) 바로 뒤에 추가:

```python
def test_consecutive_known_count_counts_trailing_known_ids():
    # 끝에서부터(가장 최근 도착 순) known인 개수만 센다.
    assert _consecutive_known_count([1, 2, 3, 4, 5], {3, 4, 5}) == 3


def test_consecutive_known_count_stops_at_first_unknown_from_the_end():
    # 끝에서 세다가 모르는 id를 만나면 거기서 멈춘다 — 더 앞쪽에 known이
    # 남아있어도 세지 않는다.
    assert _consecutive_known_count([1, 2, 3, 4, 5], {1, 2, 4, 5}) == 2


def test_consecutive_known_count_returns_zero_when_last_is_unknown():
    assert _consecutive_known_count([1, 2, 3], {1, 2}) == 0


def test_consecutive_known_count_returns_zero_for_empty_list():
    assert _consecutive_known_count([], {1, 2, 3}) == 0


def test_consecutive_known_count_counts_everything_when_all_known():
    assert _consecutive_known_count([1, 2, 3], {1, 2, 3}) == 3
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_reviews.py -k consecutive_known_count -v`
Expected: FAIL — `ImportError: cannot import name '_consecutive_known_count'`

- [ ] **Step 3: `_consecutive_known_count` 최소 구현 작성**

`backend/scrapers/baemin_reviews.py`의 모듈 상수 블록(약 90~93번째 줄, `_MAX_LOAD_MORE_CLICKS` 등이 있는 곳) 바로 뒤, `class BaeminScrapeError` 앞에 상수 하나를 추가하고, `_review_list_path` 함수(약 100~101번째 줄) 바로 뒤에 헬퍼 함수를 추가한다:

```python
_KNOWN_ID_STOP_THRESHOLD = 5
```

```python
def _consecutive_known_count(ids_in_order: list[int], existing_ids: set[int]) -> int:
    """`ids_in_order`(리뷰가 도착한 순서 — 배민 리뷰 목록이 최신순이므로
    사실상 최신순)의 끝에서부터, `existing_ids`(이미 DB에 저장된
    external_review_id)에 있는 id가 연속으로 몇 개인지 센다. 중간에
    모르는 id를 만나면 그 즉시 멈춘다 — "가장 최근에 본 것들이 전부 이미
    아는 리뷰"인지만 판단하면 되므로, 앞쪽에 known이 더 있어도 상관없다."""
    count = 0
    for review_id in reversed(ids_in_order):
        if review_id not in existing_ids:
            break
        count += 1
    return count
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_reviews.py -k consecutive_known_count -v`
Expected: PASS (5 passed)

- [ ] **Step 5: `fetch_all_reviews` 조기종료 통합 — 실패하는 테스트 작성**

`test_baemin_reviews.py`의 `test_fetch_all_reviews_stops_load_more_after_two_consecutive_no_progress_clicks` 함수(약 450번째 줄) 바로 앞에 추가:

```python
def test_fetch_all_reviews_stops_before_any_click_when_initial_load_is_all_known():
    # 초기 자동 로드 안에서 신규 1건 + 이미 아는 5건이 한 번에 온 경우,
    # "더보기"를 단 한 번도 클릭하지 않고 끝나야 한다 — 이미 5연속 known을
    # 확인했으므로 그 이상 조회할 필요가 없다고 판단한다.
    known_ids = {100, 101, 102, 103, 104}

    class _Page(_FakePage):
        def wait_for_timeout(self, ms):
            if not getattr(self, "_fired", False):
                self._fired = True
                reviews = [{**_RAW_REVIEW, "id": 999}] + [
                    {**_RAW_REVIEW, "id": i} for i in (100, 101, 102, 103, 104)
                ]
                self._handlers["response"](_review_response(reviews))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 6
    assert p.more_button.click_calls == 0


def test_fetch_all_reviews_keeps_paginating_when_known_run_is_interrupted():
    # known id들이 연속되지 않고 중간에 신규 리뷰가 끼어 있으면(전체적으로는
    # known이 5개 있어도 "연속"은 아님) 정상적으로 계속 페이지네이션해야
    # 한다 — 기존 "더보기" 종료 조건(연속 2회 무진행)만 적용된다.
    known_ids = {100, 101, 102, 103, 104}

    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                reviews = [
                    {**_RAW_REVIEW, "id": 100}, {**_RAW_REVIEW, "id": 101},
                    {**_RAW_REVIEW, "id": 999},  # 연속을 끊는 신규 리뷰
                    {**_RAW_REVIEW, "id": 102}, {**_RAW_REVIEW, "id": 103},
                ]
                self._handlers["response"](_review_response(reviews))
            # 이후 클릭에서는 응답 없음 — 무진행 카운터로 정상 종료.

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 5
    assert p.more_button.click_calls == 2  # 연속 2회 무진행으로 종료(기존 규칙)


def test_fetch_all_reviews_stops_mid_pagination_once_five_consecutive_known_seen():
    # 초기 로드는 전부 신규라 계속 진행하다가, 두 번째 "더보기" 클릭에서
    # 받은 응답이 이미 아는 리뷰 5개 연속이면 그 시점에서 멈춰야 한다 —
    # 세 번째 클릭은 일어나면 안 된다.
    known_ids = {200, 201, 202, 203, 204}

    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([{**_RAW_REVIEW, "id": 999}]))
            elif self._wait_count == 2:
                reviews = [{**_RAW_REVIEW, "id": i} for i in (200, 201, 202, 203, 204)]
                self._handlers["response"](_review_response(reviews))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO, existing_ids=known_ids)

    assert len(result) == 6
    assert p.more_button.click_calls == 2  # 초기 로드 + 1번 더보기 클릭 후 조기 종료


def test_fetch_all_reviews_with_no_existing_ids_behaves_exactly_as_before():
    # existing_ids를 안 넘기면(기본값 None) 기존 동작(연속 2회 무진행까지
    # 계속 페이지네이션)과 완전히 동일해야 한다 — 최초 동기화 경로 회귀 방지.
    class _Page(_FakePage):
        def __init__(self):
            super().__init__()
            self._wait_count = 0

        def wait_for_timeout(self, ms):
            self._wait_count += 1
            if self._wait_count == 1:
                self._handlers["response"](_review_response([_RAW_REVIEW]))

    p = _Page()
    result = fetch_all_reviews(p, shop_no=_SHOP_NO)

    assert len(result) == 1
    assert p.more_button.click_calls == 2
```

- [ ] **Step 6: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_reviews.py -k "stops_before_any_click or keeps_paginating_when_known or stops_mid_pagination or with_no_existing_ids" -v`
Expected: FAIL — `TypeError: fetch_all_reviews() got an unexpected keyword argument 'existing_ids'`

- [ ] **Step 7: `fetch_all_reviews` 최소 구현 작성**

`backend/scrapers/baemin_reviews.py`의 `fetch_all_reviews` 함수(약 104번째 줄)를 아래로 교체한다 — 시그니처, docstring, `collected`/`arrival_order` 추적, "더보기" 루프 안의 조기종료 체크만 바뀐다(나머지 backdrop/무진행/하드캡 로직은 그대로):

```python
def fetch_all_reviews(page, shop_no: int, existing_ids: set[int] | None = None) -> list[dict]:
    """`page`가 리뷰관리 화면을 로드하며 organically 발생시키는 리뷰 리스트
    응답을 가로채 수집한다. 우리는 요청을 직접 만들지 않는다 (모듈 docstring
    참고 — raw fetch()는 CORS로 차단된다).

    `existing_ids`(이미 DB에 저장된 external_review_id 집합)를 넘기면,
    도착 순서(최신순)로 봤을 때 이미 아는 id가 연속 `_KNOWN_ID_STOP_THRESHOLD`개
    나오는 순간 더 이상 "더보기"를 클릭하지 않고 종료한다 — 두 번째 이후
    동기화에서 매번 전체 리뷰 이력을 다시 훑지 않기 위한 증분 최적화다
    (설계 문서 참고). 안 넘기면(기본값 `None`) 기존 동작과 완전히 동일하게
    끝까지(무진행 2회 또는 하드캡) 페이지네이션한다."""
    existing_ids = existing_ids or set()
    path = _review_list_path(shop_no)
    collected: dict[int, dict] = {}
    arrival_order: list[int] = []
    state = {"observed_review_endpoint": False}

    def _on_response(response) -> None:
        url = response.url
        if urlparse(url).path != path:
            return
        # 상태 코드와 무관하게 "리뷰 목록 엔드포인트 자체는 응답을 줬다"는
        # 사실은 기록한다 — 401/500이 와도 우리가 올바른 엔드포인트를
        # 찾긴 했다는 뜻이므로, 아래 200 파싱 실패와는 구분해야 한다.
        state["observed_review_endpoint"] = True
        if response.status != 200:
            return
        try:
            body = response.json()
        except Exception:
            return
        for raw in body.get("reviews", []):
            if raw["id"] not in collected:
                arrival_order.append(raw["id"])
            collected[raw["id"]] = raw

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/reviews")
        except Exception as e:
            raise BaeminScrapeError(f"리뷰 페이지 이동에 실패했습니다: {e}") from e

        # 리뷰관리 화면은 진입 즉시(상호작용 없이) 최근 리뷰 2페이지를
        # organic하게 로드한다 — 완료를 알리는 신호가 따로 없어 유한 시간만큼
        # 기다린다.
        page.wait_for_timeout(_INITIAL_LOAD_WAIT_MS)

        # 추가 페이지네이션은 리스트 하단의 "더보기" 버튼을 반복 클릭해
        # 트리거한다(모듈 docstring 참고 — 스크롤이 아니라 명시적 버튼).
        # 버튼은 리스트가 끝에 도달해도 사라지지 않는 것으로 관찰돼
        # count() == 0은 보너스 조기 종료일 뿐, 연속 무진행 카운터가 실질적인
        # 종료 조건이다.
        consecutive_no_progress = 0
        for _ in range(_MAX_LOAD_MORE_CLICKS):
            if _consecutive_known_count(arrival_order, existing_ids) >= _KNOWN_ID_STOP_THRESHOLD:
                break
            more_button = page.get_by_text("더보기", exact=True)
            if more_button.count() == 0:
                break
            before = len(collected)
            # 로그인 직후에만 뜨는 게 아니라, 리뷰 목록을 계속 불러오는 도중에도
            # 같은 종류의 프로모션 backdrop이 다시 뜰 수 있다(실 계정 재현 확인 —
            # 이게 뜬 채로 방치하면 "더보기" 클릭이 계속 막혀서 실제로는 훨씬 더
            # 많은 리뷰가 남아있는데도 무진행으로 오판해 조기 종료하게 된다). 그래서
            # 클릭 시도 직전마다 매번 방어적으로 확인·해제한다.
            if page.get_by_test_id("backdrop").count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            try:
                more_button.first.scroll_into_view_if_needed()
                more_button.first.click(timeout=5_000)
            except PlaywrightTimeoutError:
                # 클릭 자체가 타임아웃 나도(예: backdrop이 클릭 순간에 막 뜬 경우)
                # 이미 로딩이 시작됐을 수 있으므로 즉시 포기하지 않는다 — backdrop을
                # 한 번 더 확인해서 열려 있으면 닫고, 이번 라운드는 대기만 하고
                # 다음 루프에서 다시 시도한다.
                if page.get_by_test_id("backdrop").count() > 0:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            page.wait_for_timeout(_LOAD_MORE_WAIT_MS)
            if len(collected) > before:
                consecutive_no_progress = 0
            else:
                consecutive_no_progress += 1
                if consecutive_no_progress >= _MAX_CONSECUTIVE_NO_PROGRESS:
                    break
    finally:
        page.remove_listener("response", _on_response)

    # 리뷰 목록 엔드포인트 응답을 한 번도 관측하지 못했다면 — URL 패턴 변경,
    # 클라이언트 사이드 404, 모든 요청이 인증 만료로 실패하는 경우 등 — 이건
    # "리뷰 0건"과 절대 같은 의미가 아니다. 조용히 빈 리스트를 반환하면 이번
    # 동기화 오류 신고 작업 전체가 무력화되므로 명시적으로 실패시킨다.
    if not state["observed_review_endpoint"]:
        raise BaeminScrapeError("리뷰 목록 API 응답을 한 번도 확인하지 못했습니다")

    # 리뷰 0건은 매장에 리뷰가 아직 없다는 뜻일 수도 있는 정상 데이터다 —
    # 엔드포인트 응답을 최소 한 번 관측했다면(위 체크 통과) 여기서는 에러로
    # 취급하지 않는다.
    return list(collected.values())
```

- [ ] **Step 8: 전체 리뷰 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_baemin_reviews.py -v`
Expected: PASS (기존 테스트 전부 + 신규 9개 전부)

- [ ] **Step 9: 커밋**

```bash
cd backend && git add scrapers/baemin_reviews.py tests/test_baemin_reviews.py
git commit -m "feat: 리뷰 페이지네이션에 연속 known-id 조기종료 추가

이미 저장된 external_review_id가 도착 순서(최신순) 기준 연속 5개 나오면
'더보기' 클릭을 멈춘다. existing_ids를 안 넘기면 기존 동작과 100% 동일."
```

---

### Task 4: `_run_sync` 통합 — 5개 소스 전부 증분 조회로 연결

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes:
  - `compute_settlement_sync_range(latest_settled_date, today, *, backfill_days) -> tuple[date, date]` (Task 1)
  - `filter_months_needing_sync(months, synced_months, *, always_include=None) -> list[str]` (Task 2)
  - `fetch_all_reviews(page, shop_no, existing_ids=None) -> list[dict]` (Task 3)
- Produces: `_run_sync`의 새 동작 — 아래 Step들이 그대로 최종 코드다. 이후 다른 태스크가 이 인터페이스를 더 소비하지 않는다(이 플랜의 마지막 코드 변경).

**중요 — 이 태스크를 시작하기 전에 먼저 할 일**: `backend/tests/test_review_sync.py`에는 `fetch_all_reviews`를 `lambda page, shop_no: [...]` 형태(정확히 2개 위치 인자, 추가 인자 없음)로 monkeypatch하는 곳이 30곳 이상 있다. Task 3에서 `fetch_all_reviews`가 `existing_ids` 키워드 인자를 받게 됐고, 이 태스크에서 `_run_sync`이 실제로 `existing_ids=existing_ids`를 넘기며 호출하도록 바꾸면, 이 30곳의 람다가 전부 `TypeError: <lambda>() got an unexpected keyword argument 'existing_ids'`로 깨진다. Step 1에서 이걸 먼저 기계적으로 고친다.

- [ ] **Step 1: 기존 `fetch_all_reviews` monkeypatch 람다를 새 시그니처와 호환되게 일괄 수정**

```bash
cd backend
sed -i '' 's/lambda page, shop_no:/lambda page, shop_no, **kwargs:/g' tests/test_review_sync.py
```

(macOS `sed -i ''` 문법 — Linux라면 `sed -i` 그대로.) 이 치환은 `lambda page, shop_no:`로 정확히 끝나는(추가 파라미터 없는) 패턴만 바꾼다 — `fetch_shop_stats`/`fetch_brand_click_metrics` 등 이미 3개 인자를 받는 람다(`lambda page, shop_no, months:`)는 이 패턴과 안 겹치므로 건드리지 않는다.

바꾼 뒤 확인:

```bash
grep -c "lambda page, shop_no, \*\*kwargs:" tests/test_review_sync.py
grep -c "lambda page, shop_no:" tests/test_review_sync.py
```

Expected: 첫 명령은 30 이상, 두 번째 명령은 `0`(더 이상 옛 패턴이 없어야 함).

- [ ] **Step 2: 치환 후 기존 테스트가 여전히 전부 통과하는지 확인(회귀 없음 확인)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -v`
Expected: PASS — 이 시점엔 아직 `_run_sync`을 안 바꿨으므로(여전히 `fetch_all_reviews(session.page, shop_no)` 2-인자 호출) 전부 그대로 통과해야 한다. 여기서 실패하면 Step 1의 sed가 잘못된 것이므로 다음 단계로 넘어가지 않는다.

- [ ] **Step 3: 증분 매출/재주문율(가게통계) 통합 — 실패하는 테스트 작성**

`test_review_sync.py`에서 `sync_setup` 픽스처(약 55번째 줄) 안의 `fetch_shop_stats` monkeypatch를, 실제로 어떤 `months`를 받았는지 검사할 수 있게 캡처하도록 살짝 바꾼다. 픽스처 자체는 그대로 두되(기본값은 계속 빈 리스트 반환), 아래 새 테스트에서 로컬로 재정의한다.

`test_review_sync.py` 끝부분(파일의 마지막 테스트 함수 뒤)에 추가:

```python
def test_sync_skips_already_synced_months_for_sales_but_still_visits_one_month_for_crm(
    db_session, sync_setup, monkeypatch,
):
    """3개월 전부(이번 달 포함 — 이번 달 몫은 "이번 달 매출 보완"이라는
    별도 경로로도 채워질 수 있으므로, 필터 입장에선 이번 달도 이미 동기화된
    것으로 보일 수 있다) 이미 sales_amount로 채워져 있으면, 필터링 결과가
    완전히 비어버린다 — 이때 fetch_shop_stats에 빈 목록을 넘기지 않고
    crmInfo 캡처를 위해 최소 1개월(가장 최근 완료된 달)은 넘겨야 한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)  # 예: ["2026-06", "2026-07", "2026-08"]
    for m in months:  # 3개월 전부 이미 있다고 가정 → 필터 결과가 빈 리스트가 됨
        db_session.add(DailySettlement(
            store_id=job.store_id, platform_id=job.platform_id,
            settle_date=date.fromisoformat(f"{m}-15"), sales_amount=100000, deposit_amount=0,
        ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 이미 동기화된 2개월은 빠지고, crmInfo 보장용으로 마지막 완료 달(3번째 달
    # 바로 앞, 즉 months[-2]) 하나만 남아야 한다 — months[-1](이번 달)은
    # 가게통계 화면 자체가 선택 불가능해서 애초에 대상이 아니다.
    assert received_months == [[months[-2]]]


def test_sync_fetches_only_unsynced_months_when_some_are_missing(db_session, sync_setup, monkeypatch):
    """3개월 중 1개월만 이미 있으면, 나머지(아직 없는 달)만 fetch_shop_stats에
    넘겨야 한다 — crmInfo 보장 fallback은 fetch할 달이 이미 있을 때는
    끼어들지 않는다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date.fromisoformat(f"{months[0]}-15"), sales_amount=50000, deposit_amount=0,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [[months[1], months[2]]]


def test_sync_fetches_all_months_on_first_sync_when_nothing_stored_yet(db_session, sync_setup, monkeypatch):
    """최초 동기화(daily_settlements에 이 store+platform 행이 전혀 없음)는
    기존과 동일하게 3개월 전부를 fetch_shop_stats에 넘겨야 한다 — 회귀
    방지용 테스트."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received_months = []

    def _fetch_shop_stats(page, shop_no, requested_months):
        received_months.append(requested_months)
        return [], []

    monkeypatch.setattr(review_sync_mod, "fetch_shop_stats", _fetch_shop_stats)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [months]
```

- [ ] **Step 4: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "skips_already_synced_months or fetches_only_unsynced or fetches_all_months_on_first_sync" -v`
Expected: FAIL — `assert received_months == [...]`에서 실제 값이 항상 `[months]`(필터링 안 됨)로 나와 처음 두 테스트가 실패. 세 번째(최초 동기화) 테스트는 이미 통과할 수 있다(우연히 현재 동작과 같으므로) — 그래도 이번 단계에서 실행해 베이스라인을 확인해둔다.

- [ ] **Step 5: `_run_sync`에 매출/재주문율 필터링 연결**

`backend/app/review_sync.py` 상단 import 블록(약 31~48번째 줄, `from scrapers.baemin_stats import (...)`)에 `compute_settlement_sync_range`와 `filter_months_needing_sync`를 추가한다(알파벳 순 유지):

```python
from scrapers.baemin_stats import (
    ORDER_BACKFILL_PAGE_CLICKS,
    BaeminStatsScrapeError,
    compute_order_sync_range,
    compute_repurchase_rates,
    compute_settlement_sync_range,
    fetch_account_settlement,
    fetch_orders,
    fetch_settlement_breakdown_details,
    fetch_shop_stats,
    filter_months_needing_sync,
    map_deposits_by_date,
    map_order_rows,
    map_orders_to_daily_sales,
    map_repurchase_by_date,
    map_sales_by_date,
    map_settlement_breakdown_by_date,
    parse_baemin_datetime,
    recent_months,
)
```

`_run_sync` 안의 아래 블록(현재 코드, 약 339~349번째 줄):

```python
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
```

를 아래로 교체한다:

```python
        stats_errors: list[str] = []
        months = recent_months(3)
        synced_sales_dates = db.scalars(
            select(DailySettlement.settle_date).where(
                DailySettlement.store_id == job.store_id,
                DailySettlement.platform_id == job.platform_id,
                DailySettlement.sales_amount.isnot(None),
            )
        ).all()
        synced_sales_months = {d.strftime("%Y-%m") for d in synced_sales_dates}
        sales_months_to_fetch = filter_months_needing_sync(months, synced_sales_months)
        if not sales_months_to_fetch:
            # crmInfo(재주문율)는 날짜 소급이 안 되는 "최근 7일" 고정
            # 스냅샷이라, 매출을 전부 건너뛰어도 최소 한 달은 방문해서
            # 갱신해야 한다. months[-1](이번 달)은 가게통계 화면 구조상
            # 애초에 선택 불가능하므로(recent_months 문서 참고) 그 앞
            # 달(가장 최근 완료된 달)을 쓴다.
            sales_months_to_fetch = [months[-2]]
        sales_responses: list[dict] = []
        crm_responses: list[dict] = []
        for shop_no, shop_name in session.shops:
            try:
                s, c = fetch_shop_stats(session.page, shop_no, sales_months_to_fetch)
                sales_responses.extend(s)
                crm_responses.extend(c)
            except (BaeminStatsScrapeError, KeyError) as e:
                stats_errors.append(f"{shop_name}: {e}")
```

- [ ] **Step 6: 매출/재주문율 필터링 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "skips_already_synced_months or fetches_only_unsynced or fetches_all_months_on_first_sync" -v`
Expected: PASS (3 passed)

- [ ] **Step 7: 리뷰 조기종료 연결 — 실패하는 테스트 작성**

Step 3와 같은 위치(파일 끝)에 추가:

```python
def test_sync_passes_existing_review_ids_to_fetch_all_reviews(db_session, sync_setup, monkeypatch):
    """_run_sync은 이미 이 계정에 저장된 external_review_id 전체 집합을
    fetch_all_reviews에 그대로 넘겨야 한다 — 리뷰 조기종료가 실제로
    동작하려면 이 배선이 맞아야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="기존메뉴",
        external_review_id=1001, rating=5, content="이미 있는 리뷰", customer_nickname="기존고객",
        customer_order_count=1, created_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received = {}

    def _fetch_all_reviews(page, shop_no, existing_ids=None):
        received["existing_ids"] = existing_ids
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", _fetch_all_reviews)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["existing_ids"] == {1001}
```

- [ ] **Step 8: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k passes_existing_review_ids -v`
Expected: FAIL — `assert received["existing_ids"] == {1001}`에서 실제 값이 `None`(아직 안 넘김).

- [ ] **Step 9: `_run_sync`의 `fetch_all_reviews` 호출에 `existing_ids` 연결**

`_run_sync` 안의 아래 줄(현재 코드, 약 286번째 줄):

```python
                raw_reviews = fetch_all_reviews(session.page, shop_no)
```

를:

```python
                raw_reviews = fetch_all_reviews(session.page, shop_no, existing_ids=existing_ids)
```

로 교체한다(`existing_ids`는 이미 `_run_sync` 초반, 약 260번째 줄에서 계산돼 있는 변수를 그대로 재사용 — 새 변수 추가 없음).

- [ ] **Step 10: 리뷰 조기종료 연결 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k passes_existing_review_ids -v`
Expected: PASS

- [ ] **Step 11: 입금(정산내역) 증분 — 실패하는 테스트 작성**

파일 끝에 추가:

```python
def test_sync_narrows_deposit_fetch_range_when_cursor_exists(db_session, sync_setup, monkeypatch):
    """이미 deposit_amount가 채워진 가장 최근 날짜가 있으면, 90일 전체가
    아니라 그 날짜-2일부터만 fetch_account_settlement에 넘겨야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date(2026, 8, 10), sales_amount=0, deposit_amount=50000,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["range"][0] == "2026-08-08"  # 8/10 - 2일


def test_sync_uses_full_ninety_day_window_when_no_deposit_cursor_yet(db_session, sync_setup, monkeypatch):
    """이 store+platform에 deposit_amount가 채워진 행이 하나도 없으면(최초
    동기화) 기존과 동일하게 90일 전체를 조회해야 한다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received = {}

    def _fetch_account_settlement(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_account_settlement", _fetch_account_settlement)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    expected_start = (date.today() - timedelta(days=90)).isoformat()
    assert received["range"][0] == expected_start
```

- [ ] **Step 12: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "narrows_deposit_fetch_range or full_ninety_day_window" -v`
Expected: 첫 번째는 FAIL(항상 90일 전체가 넘어감), 두 번째는 이미 PASS일 수 있음(현재도 90일 전체가 기본이므로) — 베이스라인 확인용으로 같이 실행한다.

- [ ] **Step 13: `_run_sync`에 입금 증분 범위 연결**

현재 코드(약 423~447번째 줄):

```python
        today = date.today()
        try:
            window_start = today - timedelta(days=90)
            settlement_responses = fetch_account_settlement(
                session.page, window_start.isoformat(), today.isoformat(),
            )
            # ...(주석 그대로)...
            db.execute(
                update(DailySettlement)
                .where(
                    DailySettlement.store_id == job.store_id,
                    DailySettlement.platform_id == job.platform_id,
                    DailySettlement.settle_date >= window_start,
                    DailySettlement.settle_date <= today,
                )
                .values(deposit_amount=0)
            )
```

를 아래로 교체(`window_start`/`today` 계산부와 이후 `settlement_responses`/reset 로직에서 쓰는 `window_start`/`today` 변수명은 그대로 유지 — 계산 방식만 바뀐다):

```python
        today = date.today()
        try:
            latest_deposit_date = db.scalar(
                select(func.max(DailySettlement.settle_date)).where(
                    DailySettlement.store_id == job.store_id,
                    DailySettlement.platform_id == job.platform_id,
                    DailySettlement.deposit_amount.isnot(None),
                )
            )
            window_start, window_end = compute_settlement_sync_range(
                latest_deposit_date, today, backfill_days=90,
            )
            settlement_responses = fetch_account_settlement(
                session.page, window_start.isoformat(), window_end.isoformat(),
            )
            # 배민 정산은 배치 지급 캘린더라 주말/공휴일 등 실제 배치가 없는
            # 날짜는 fetch 응답에 아예 등장하지 않는다. 그런 갭 날짜의 기존
            # daily_settlements 행을 그대로 두면 시드 때 넣어둔 Mock
            # deposit_amount가 영원히 남아 실데이터와 조용히 섞인다. 그래서
            # 실제 배치를 적용하기 전에, 이번에 조회를 시도한 날짜 범위
            # (store_id+platform_id로 엄격히 스코프, 다른 플랫폼/범위 밖
            # 날짜는 절대 건드리지 않음) 안의 기존 행부터 0으로 초기화한다 —
            # 갭 날짜는 결과적으로 0(입금 없음)으로 남고, 실제 배치가 있는
            # 날짜만 아래 루프가 다시 실제 금액으로 채운다. 이 리셋 범위도
            # window_start/window_end로 좁아진 증분 조회 범위를 그대로
            # 따라간다 — 조회 안 한 과거 날짜의 기존 값을 잘못 지우지 않는다.
            db.execute(
                update(DailySettlement)
                .where(
                    DailySettlement.store_id == job.store_id,
                    DailySettlement.platform_id == job.platform_id,
                    DailySettlement.settle_date >= window_start,
                    DailySettlement.settle_date <= window_end,
                )
                .values(deposit_amount=0)
            )
```

(이 블록 뒤에 이어지는 `daily_deposits = map_deposits_by_date(...)` 이하 코드는 수정 없이 그대로 둔다.)

- [ ] **Step 14: 입금 증분 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "narrows_deposit_fetch_range or full_ninety_day_window" -v`
Expected: PASS (2 passed)

- [ ] **Step 15: 정산 상세 증분 — 실패하는 테스트 작성**

파일 끝에 추가:

```python
def test_sync_narrows_settlement_breakdown_range_when_cursor_exists(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    db_session.add(DailySettlement(
        store_id=job.store_id, platform_id=job.platform_id,
        settle_date=date(2026, 8, 12), sales_amount=0, deposit_amount=0,
        commission_amount=1000, delivery_fee_amount=500,
        customer_discount_amount=0, ad_cost_amount=0,
    ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received = {}

    def _fetch_settlement_breakdown_details(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _fetch_settlement_breakdown_details)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received["range"][0] == "2026-08-10"  # 8/12 - 2일


def test_sync_uses_full_thirty_day_window_when_no_breakdown_cursor_yet(db_session, sync_setup, monkeypatch):
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received = {}

    def _fetch_settlement_breakdown_details(page, start_date, end_date, **kwargs):
        received["range"] = (start_date, end_date)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_settlement_breakdown_details", _fetch_settlement_breakdown_details)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    expected_start = (date.today() - timedelta(days=30)).isoformat()
    assert received["range"][0] == expected_start
```

- [ ] **Step 16: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "narrows_settlement_breakdown_range or full_thirty_day_window" -v`
Expected: 첫 번째는 FAIL, 두 번째는 이미 PASS일 수 있음(베이스라인 확인용).

- [ ] **Step 17: `_run_sync`에 정산 상세 증분 범위 연결**

현재 코드(약 468~472번째 줄):

```python
        try:
            detail_window_start = today - timedelta(days=30)
            breakdown_details = fetch_settlement_breakdown_details(
                session.page, detail_window_start.isoformat(), today.isoformat(),
            )
```

를:

```python
        try:
            latest_breakdown_date = db.scalar(
                select(func.max(DailySettlement.settle_date)).where(
                    DailySettlement.store_id == job.store_id,
                    DailySettlement.platform_id == job.platform_id,
                    DailySettlement.commission_amount.isnot(None),
                )
            )
            detail_window_start, detail_window_end = compute_settlement_sync_range(
                latest_breakdown_date, today, backfill_days=30,
            )
            breakdown_details = fetch_settlement_breakdown_details(
                session.page, detail_window_start.isoformat(), detail_window_end.isoformat(),
            )
```

로 교체한다(이 블록 뒤 `breakdown_by_date = map_settlement_breakdown_by_date(...)` 이하는 그대로 둔다).

- [ ] **Step 18: 정산 상세 증분 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "narrows_settlement_breakdown_range or full_thirty_day_window" -v`
Expected: PASS (2 passed)

- [ ] **Step 19: 우가클 증분 — 실패하는 테스트 작성**

파일 끝에 추가:

```python
def test_sync_skips_already_synced_click_metric_months_but_always_includes_current_month(
    db_session, sync_setup, monkeypatch,
):
    """브랜드별 우가클도 매출과 같은 원리지만, 진행 중인 이번 달은 이미
    행이 있어도 항상 재조회 대상에 포함해야 한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)
    current_month = months[-1]
    shop_no = "99999001"  # _FakeSession.shops의 shop_no와 동일해야 함
    for m in months:  # 3개월 전부 이미 있다고 가정(이번 달 포함)
        db_session.add(BrandAdClickMetric(
            store_id=job.store_id, platform_id=job.platform_id, shop_no=shop_no,
            metric_date=date.fromisoformat(f"{m}-10"),
            ad_spend=100, impressions=10, clicks=1, ad_orders=0, ad_revenue=0,
        ))
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received_months = []

    def _fetch_brand_click_metrics(page, shop_no, requested_months):
        received_months.append(requested_months)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_brand_click_metrics)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    # 완료된 2개월은 건너뛰고, 진행 중인 이번 달만 남아야 한다.
    assert received_months == [[current_month]]


def test_sync_fetches_all_click_metric_months_on_first_sync(db_session, sync_setup, monkeypatch):
    """이 shop_no에 brand_ad_click_metrics 행이 전혀 없으면(최초 동기화)
    기존과 동일하게 3개월 전부를 넘겨야 한다."""
    import app.review_sync as review_sync_mod
    from scrapers.baemin_stats import recent_months

    job, conn = sync_setup
    months = recent_months(3)

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)

    received_months = []

    def _fetch_brand_click_metrics(page, shop_no, requested_months):
        received_months.append(requested_months)
        return []

    monkeypatch.setattr(review_sync_mod, "fetch_brand_click_metrics", _fetch_brand_click_metrics)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    assert received_months == [months]
```

- [ ] **Step 20: 테스트가 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "skips_already_synced_click_metric or fetches_all_click_metric_months" -v`
Expected: 첫 번째는 FAIL(필터링 안 됨), 두 번째는 이미 PASS일 수 있음.

- [ ] **Step 21: `_run_sync`에 우가클 증분 필터링 연결**

현재 코드(약 492~494번째 줄):

```python
        for shop_no, shop_name in session.shops:
            try:
                click_responses = fetch_brand_click_metrics(session.page, shop_no, months)
                click_by_date = map_click_metrics_by_date(click_responses)
```

를:

```python
        current_month = date.today().strftime("%Y-%m")
        for shop_no, shop_name in session.shops:
            try:
                synced_click_dates = db.scalars(
                    select(BrandAdClickMetric.metric_date).where(
                        BrandAdClickMetric.store_id == job.store_id,
                        BrandAdClickMetric.platform_id == job.platform_id,
                        BrandAdClickMetric.shop_no == str(shop_no),
                    )
                ).all()
                synced_click_months = {d.strftime("%Y-%m") for d in synced_click_dates}
                click_months_to_fetch = filter_months_needing_sync(
                    months, synced_click_months, always_include={current_month},
                )
                click_responses = fetch_brand_click_metrics(session.page, shop_no, click_months_to_fetch)
                click_by_date = map_click_metrics_by_date(click_responses)
```

로 교체한다(이 블록 뒤 `for metric_date, m in click_by_date.items(): upsert_brand_ad_click_metric(...)` 이하와, CPC 입찰가 조회 블록은 수정 없이 그대로 둔다).

- [ ] **Step 22: 우가클 증분 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "skips_already_synced_click_metric or fetches_all_click_metric_months" -v`
Expected: PASS (2 passed)

- [ ] **Step 23: 전체 백엔드 테스트 스위트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS, 실패 0건(이 태스크 이전 스위트가 전부 통과하는 상태였으므로, 새로 추가한 테스트를 포함해 전부 통과해야 한다).

- [ ] **Step 24: 커밋**

```bash
cd backend && git add app/review_sync.py tests/test_review_sync.py
git commit -m "feat: 데이터 동기화 5개 소스를 증분 조회로 전환

리뷰(연속 known-id 조기종료), 월별 매출/우가클(이미 있는 달 건너뛰기,
재주문율은 최소 1개월 보장/우가클 이번 달은 항상 포함), 입금/정산상세
(커서-2일)를 orders와 동일한 원칙으로 연결한다. 최초 동기화(커서 없음)
동작은 전부 회귀 테스트로 그대로 유지되는 것을 확인했다."
```

---

### Task 5: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 없음(문서 작업)
- Produces: 없음(이 플랜의 마지막 태스크)

- [ ] **Step 1: "배민 매출·입금·재주문율 연동" 절 뒤에 새 절 추가**

`CLAUDE.md`에서 `### 배민 정산 상세(수수료/배달비/고객할인/우가클비용) 연동 (예외 허용)` 절 바로 앞(즉 "배민 매출·입금·재주문율 연동" 절이 끝나는 지점)에 아래 절을 추가한다:

```markdown
### 배민 데이터 동기화 증분 조회 (예외 허용 아님 — 순수 성능 개선)
원래 "데이터 동기화"는 리뷰/월별매출(가게통계)/입금(정산내역)/정산상세
(카드클릭)/우리가게클릭 5개 소스를 매 실행마다 전부 다시 긁었다(개별
주문내역만 유일하게 증분). 실제 배포 환경 첫 실측(2026-08-19, 데모 계정)
에서 22분이 걸린 걸 확인하고, orders가 이미 쓰는 "DB에서 커서를 계산해
그 이후만 조회" 패턴을 나머지 5개에도 확장했다. 새 컬럼/테이블은 없다 —
매번 `MAX(날짜)`/해당 컬럼 `IS NOT NULL` 존재 여부로 커서를 그때그때
계산한다(정규화 원칙 유지). 리뷰는 도착 순서(최신순) 기준 이미 아는
`external_review_id`가 연속 5개 나오면 "더보기" 클릭을 멈춘다
(`_consecutive_known_count`, `backend/scrapers/baemin_reviews.py`).
월별 매출·우가클은 `filter_months_needing_sync`로 이미 데이터가 있는
달을 건너뛰되, 재주문율(crmInfo)은 날짜 소급이 안 되는 "최근 7일" 고정
스냅샷이라 매출을 전부 건너뛰어도 매번 최소 1개월은 방문해서 갱신하고,
우가클의 진행 중인 이번 달은 항상 재조회 대상에 포함한다. 입금/정산
상세는 `compute_settlement_sync_range`(orders의 `compute_order_sync_range`와
동일한 커서-2일 패턴)로 조회 폭을 좁힌다. 최초 동기화(커서/기존 행 없음)
동작은 전부 그대로다 — 이 변경은 두 번째 이후 동기화만 빠르게 만든다.
버튼을 안 눌러도 자동으로 도는 백그라운드 스케줄러는 별도 설계 대상으로
아직 범위 밖이다. 설계 상세는
`docs/superpowers/specs/2026-08-19-baemin-sync-incremental-fetch-design.md`
참고.
```

- [ ] **Step 2: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: 배민 데이터 동기화 증분 조회 CLAUDE.md 반영"
```

---

## 최종 확인 (모든 태스크 완료 후)

- [ ] `cd backend && .venv/bin/python -m pytest -q` 전부 PASS
- [ ] `git log --oneline -6`으로 5개(또는 6개, CLAUDE.md 포함) 커밋이 순서대로 쌓였는지 확인
- [ ] 설계 문서의 "범위 밖" 절에 명시된 대로, 이 플랜은 스케줄러를 포함하지 않는다 — 다음 단계(별도 브레인스토밍)로 자연스럽게 이어진다.
