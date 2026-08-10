"""배민 사장님광장의 가게통계(매출/재주문율)·정산내역(입금) API 응답을
날짜별로 집계하는 순수 함수와, 그 organic 응답을 실제로 캡처하는
`fetch_shop_stats`/`fetch_account_settlement`.

브랜드(치밥대장 등)별로 분리하지 않고 전부 날짜 단위로 합산한다 — 설계
결정(계정 전체 합산만 지원, 입금이 애초에 브랜드별로 안 나뉘어 나오기
때문). 세 함수 모두 여러 브랜드/여러 달/여러 페이지에 걸친 raw 응답
리스트를 받아 하나의 날짜별 dict로 합친다.

### fetch 함수의 화면 조작 방식 (실 계정 조사로 확인, 2026-08-11)

인증은 `baemin_auth.login()`이 반환한 세션이 담당한다. `baemin_reviews.py`와
같은 이유로 우리가 직접 API를 호출하지 않고, 인증된 `page`가 화면을 조작하며
스스로 발생시키는(organic) 서명된 응답을 `page.on("response", ...)`로
가로챈다.

**가게통계(`/shops/{shop_no}/stat`) 월 선택**: 상단의 "N월" 헤더 텍스트를
클릭하면 "기간" 바텀시트가 열린다(최근 7일/최근 30일/월별 조회 라디오 +
현재 선택된 "YYYY년 M월" 버튼, `aria-haspopup="dialog"`). 그 버튼을 클릭하면
그 위에 또 다른 다이얼로그가 열리고, 선택 가능한 월들이 평범한 텍스트
"YYYY년 M월"(월은 0패딩 없음) 목록으로 나온다. 원하는 월 텍스트를 클릭하면
그 목록 다이얼로그는 닫히고 "기간" 다이얼로그로 돌아오며, 거기서 "적용"
버튼을 눌러야 실제로 반영된다.

**중요한 discrepancy (설계 문서/브리프의 가정과 다름)**: 이 목록은 진행 중인
이번 달을 포함하지 않는다 — "최근 3개월 동안의 내역만 볼 수 있어요" 문구
그대로, 완료된 지난 3개월만 나온다(2026-08-11 실측: today가 2026-08-11일 때
목록에는 2026-05/06/07만 있고 2026-08은 없음). 게다가 페이지를 처음 로드했을
때(어떤 상호작용도 하기 전)의 기본 상태도 "이번 달" 데이터가 아니라 계정에
persist된 마지막으로 조회했던 달이었다(실측 시점엔 우연히 2026-07 —
이전 조사 세션들이 그 상태를 남겨뒀을 뿐이지 "이번 달"이라서가 아니다).
그래서 이 모듈은 (1) 초기 로드 시점에 뜨는 매출 응답은 무시하고(어느 달인지
예측할 수 없어 `months`에 없는 달을 중복 집계할 위험이 있다 — 아래
`fetch_shop_stats`의 `collect_sales` 플래그 참고), (2) `_select_month_dropdown`이
목록에 없는 달(진행 중인 이번 달)을 받으면 아무것도 적용하지 않고 그 달을
조용히 건너뛴다. 즉 `months`에 이번 달이 포함돼 있으면
`fetch_shop_stats`가 반환하는 `sales_responses`는 `len(months)`보다 하나
적을 수 있다 — 이는 버그가 아니라 배민 화면 자체의 제약이다(진행 중인
달의 월간 집계는 이 화면에서 볼 수 있는 방법이 없다).

**정산내역(`/orders/billing`) 날짜 범위**: "날짜 직접 선택"을 클릭하면 여는
"기간" 다이얼로그는 기본적으로 "날짜"(직접 선택) 탭이 이미 활성 상태다
(일·주/월/분기/날짜 라디오 탭 그룹에서 `directly` 값이 기본 checked) —
탭을 따로 클릭할 필요가 없다. 그 안의 날짜 범위 표시("~" 포함 텍스트)를
클릭하면 실제 두 달짜리 캘린더 그리드가 그 위에 또 열린다. 각 달은
`<table role="grid"><caption>YYYY년 M월</caption>...`로 렌더링되고, 날짜
버튼은 그 안에서 `aria-label="N일"`이다("이전 달"/"다음 달" 아이콘 버튼으로
두 달짜리 창을 이동시킨다). 시작일 버튼, 종료일 버튼을 순서대로 클릭한 뒤
캘린더 자체의 "적용"을 누르고, 이어서 상위 "기간" 다이얼로그의 "적용"도
한 번 더 눌러야 실제로 반영된다 — 두 다이얼로그가 겹쳐있는 동안 "적용"
버튼도 동시에 두 개 존재해서, 매 클릭을 정확히 그 시점의 최상단
다이얼로그로 scope하지 않으면 아래쪽 다이얼로그의 버튼을 잘못 클릭해
포인터 이벤트가 가로채이는 것으로 확인됐다.

**정산내역 페이지네이션**: 좁은 기본 범위에서는 안 보이지만, 90일로 넓히면
`/v3/settle/history/summary?...&page=0&size=10`처럼 페이지당 10건으로
잘리고, 리스트 하단에 리뷰 리스트와 동일한 "더보기" 텍스트 버튼이 나타난다
(실측 확인: 90일 범위 적용 후 렌더링된 항목이 정확히 10건이었고, 마우스
휠 스크롤만으로는 추가 페이지가 로드되지 않았다 — "더보기" 클릭이
필요하다). 그래서 `fetch_account_settlement`는 `baemin_reviews.py`의
"더보기" 반복 클릭 + 연속 무진행 카운터 패턴을 그대로 재사용한다.

**backdrop 처리 시 주의**: `data-testid="backdrop"`은 프로모션 모달
전용이 아니라, 배민 자체 디자인시스템의 모든 다이얼로그(기간 모달, 월
선택, 날짜 캘린더 등)가 열려있는 동안 공통으로 갖는 레이어다. 그래서
`_dismiss_backdrop_if_present`는 반드시 "우리 자신의 다이얼로그를 열기
전"(페이지 진입 직후, 또는 한 달/한 페이지 조회가 완전히 끝나고 다음
반복을 시작하기 전)에만 호출해야 한다 — 다이얼로그를 여는 클릭들
사이사이에 방어적으로 호출하면 방금 우리가 연 다이얼로그 자체를
Escape로 닫아버린다(실 계정 재현으로 확인된 버그 패턴).
"""

import re
from datetime import date, timedelta
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_MAX_LOAD_MORE_CLICKS = 30
_MAX_CONSECUTIVE_NO_PROGRESS = 2
_LOAD_MORE_WAIT_MS = 1_500
_MAX_CALENDAR_NAV_CLICKS = 36
_MONTH_CAPTION_RE = re.compile(r"(\d{4})년 (\d{1,2})월")


class BaeminStatsScrapeError(Exception):
    pass


def _dismiss_backdrop_if_present(page) -> None:
    # baemin_reviews.py의 페이지네이션 클릭과 동일한 방어 패턴 — 프로모션
    # 모달이 조사 도중 언제든 다시 뜰 수 있다(실 계정으로 확인됨). 단,
    # 우리 자신의 다이얼로그가 열려있는 도중에는 호출하지 않는다(모듈
    # docstring의 "backdrop 처리 시 주의" 참고).
    if page.get_by_test_id("backdrop").count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


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


def recent_months(count: int = 3) -> list[str]:
    """이번 달을 포함해 최근 `count`개월을 오래된 순으로 반환한다.
    예: 2026-08에 호출하면 ["2026-06", "2026-07", "2026-08"]. Task 3의
    `_run_sync`가 매출 백필 범위를 정할 때 그대로 가져다 쓴다.

    주의: 여기 포함된 이번 달은 `fetch_shop_stats`가 실제로 캡처하지 못할 수
    있다 — 배민 가게통계 화면의 월별 조회는 진행 중인 이번 달을 선택지로
    제공하지 않는다(모듈 docstring의 discrepancy 절 참고). 그래도 이 함수
    자체는 브리프가 정의한 대로 "이번 달 포함 최근 N개월"을 반환한다 —
    무엇을 실제로 조회할지 정하는 건 호출자의 책임이고, 이번 달이 빠질 수
    있다는 사실은 fetch 쪽에서 감내한다."""
    today = date.today()
    months: list[str] = []
    y, m = today.year, today.month
    for _ in range(count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(months))


def _select_month_dropdown(page, month: str) -> bool:
    """가게통계 화면의 월 선택 드롭다운을 `month`("YYYY-MM")로 바꾼다.
    실제 클릭 순서는 모듈 docstring 참고. 목록에 그 달이 없으면(진행 중인
    이번 달) 아무것도 적용하지 않고 다이얼로그를 닫은 뒤 False를 반환한다 —
    호출자(`fetch_shop_stats`)는 그 달을 건너뛴다."""
    header = page.locator("text=/^\\d+월$/").first
    header.click(timeout=5_000)
    page.wait_for_timeout(500)

    select_btn = page.locator("button[aria-haspopup='dialog']").filter(
        has_text=re.compile(r"\d{4}년")
    )
    select_btn.first.click(timeout=5_000)
    page.wait_for_timeout(500)

    list_dialog = page.get_by_role("dialog").last
    year_str, month_str = month.split("-")
    target_text = f"{int(year_str)}년 {int(month_str)}월"
    option = list_dialog.get_by_text(target_text, exact=True)
    if option.count() == 0:
        # 진행 중인 이번 달처럼 목록에 없는 달 — 아무것도 적용하지 않고
        # 열려있는 다이얼로그(월 목록 + 기간)를 닫아 다음 반복을 위해 깨끗한
        # 상태로 되돌린다.
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        return False

    option.first.click(timeout=5_000)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="적용").first.click(timeout=5_000)
    return True


def fetch_shop_stats(page, shop_no: int, months: list[str]) -> tuple[list[dict], list[dict]]:
    """가게통계 화면(`/shops/{shop_no}/stat`)에서 매출(statistics/orders/summary)과
    재주문율(crmInfo) organic 응답을 가로챈다. `months`에 담긴 각 달마다 월
    선택 드롭다운을 조작해 그 달 데이터를 로드시킨다 — crmInfo는 월과 무관한
    고정 최근 7일 창이라 첫 로드에서만 캡처하고 이후 월 이동에서는 무시한다.

    `months`에 진행 중인 이번 달이 포함돼 있으면 그 달은 조용히 건너뛴다
    (모듈 docstring의 discrepancy 절 참고) — 반환되는 `sales_responses`가
    `len(months)`보다 적을 수 있다.

    `crm_responses`는 빈 리스트일 수 있다 — "신규-재주문" 위젯은 화면
    하단에서 지연 로드되는데, 실 계정으로 두 차례 재현했을 때(위젯을
    스크롤로 뷰포트에 넣고 최대 8초까지 나눠 기다려도) 매번 organic 요청이
    잡히지 않았다(원인 불명, 추후 조사 필요 — 매출 엔드포인트는 동일한
    조건에서 매번 확실히 잡혔다). 그래서 매출과 달리 crmInfo 미관측은
    하드 에러로 취급하지 않는다.
    """
    sales_responses: list[dict] = []
    crm_responses: list[dict] = []
    state = {
        "observed_sales_endpoint": False,
        "observed_crm_endpoint": False,
        # 첫 로드(어떤 상호작용도 하기 전) 시점에 뜨는 매출 응답은 무시한다 —
        # 그 응답이 어느 달 데이터인지 예측할 수 없다(계정에 persist된 마지막
        # 조회 상태일 뿐, "이번 달"이라는 보장이 없다 — 모듈 docstring 참고).
        # 예측 불가능한 달을 `months`의 명시적 선택과 섞으면 같은 달이
        # 중복으로 합산될 위험이 있어, 명시적으로 월을 선택하기 시작한
        # 뒤부터만 수집한다.
        "collect_sales": False,
    }

    def _on_response(response) -> None:
        url = response.url
        path = urlparse(url).path
        if "self-api.baemin.com" not in url:
            return
        if path == "/v3/statistics/orders/summary":
            state["observed_sales_endpoint"] = True
            if response.status == 200 and state["collect_sales"]:
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

        page.wait_for_timeout(3_000)  # 첫 로드(예측 불가한 기본 달) 대기
        _dismiss_backdrop_if_present(page)
        # "신규-재주문"(crmInfo) 위젯은 화면 하단에 스켈레톤 placeholder로
        # 렌더링된 채 대기하다가 실제로 뷰포트에 들어와야 organic 요청이
        # 발생한다(실측 확인 — 스크롤 없이 3초 대기만으로는 잡히지 않았다).
        # 정확한 위젯 위치로 스크롤하고, 지연 로드가 늦게 뜨는 경우를 대비해
        # 짧게 여러 번 나눠 기다린다(고정 픽셀 스크롤보다 안정적).
        heading = page.get_by_text("신규-재주문", exact=False).first
        try:
            heading.scroll_into_view_if_needed(timeout=5_000)
        except PlaywrightTimeoutError:
            page.mouse.wheel(0, 1_200)  # 위젯 텍스트를 못 찾으면 대략적으로라도 스크롤
        for _ in range(4):
            page.wait_for_timeout(2_000)
            if state["observed_crm_endpoint"]:
                break
        state["collect_sales"] = True

        for month in months:
            try:
                selected = _select_month_dropdown(page, month)
            except PlaywrightTimeoutError as e:
                raise BaeminStatsScrapeError(f"{month} 매출 조회 중 월 선택에 실패했습니다: {e}") from e
            if not selected:
                continue
            page.wait_for_timeout(2_000)
            _dismiss_backdrop_if_present(page)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_sales_endpoint"]:
        raise BaeminStatsScrapeError("매출 통계 API 응답을 한 번도 확인하지 못했습니다")
    # crmInfo는 매출과 달리 하드 실패시키지 않는다 — 실 계정으로 두 차례
    # 재현했을 때(스크롤로 위젯을 뷰포트에 넣고 최대 8초까지 나눠 기다려도)
    # 이 화면에서 안정적으로 organic 요청을 발생시키지 못했다(원인 불명 —
    # 매출/정산 엔드포인트는 동일한 조건에서 매번 확실히 잡혔던 것과 대비됨,
    # 추가 조사 필요). crm_responses가 빈 리스트여도 map_repurchase_by_date는
    # 안전하게 빈 dict를 반환하므로(순수 함수, 빈 입력에 대해 크래시하지
    # 않음), 매출/정산이라는 이 태스크의 핵심 데이터를 재주문율 하나 때문에
    # 통째로 실패시키지 않는 쪽을 택했다.
    return sales_responses, crm_responses


def _open_date_range_picker(page) -> None:
    """정산내역 화면의 "날짜 직접 선택" 버튼을 눌러 "기간" 다이얼로그를 연다.
    실측 확인: 이 다이얼로그는 "날짜"(직접 선택) 탭이 이미 기본 활성 상태로
    열린다 — 별도로 탭을 클릭할 필요가 없다(모듈 docstring 참고)."""
    page.get_by_text("날짜 직접 선택").first.click(timeout=5_000)
    page.wait_for_timeout(800)


def _visible_month_captions(dialog) -> list[tuple[int, int]]:
    captions = dialog.locator("caption").all()
    result: list[tuple[int, int]] = []
    for c in captions:
        m = _MONTH_CAPTION_RE.match(c.inner_text(timeout=1_000))
        if m:
            result.append((int(m.group(1)), int(m.group(2))))
    return result


def _click_calendar_day(dialog, year: int, month: int, day: int) -> None:
    """열린 날짜 캘린더(두 달이 나란히 보이는 그리드)에서 특정 날짜를
    클릭한다. 각 달은 `<table role="grid"><caption>YYYY년 M월</caption>...`로
    렌더링되고 날짜 버튼은 그 안에서 `aria-label="N일"`이다(모듈 docstring
    참고). "이전 달"/"다음 달"로 두 달짜리 창을 목표 달이 보일 때까지 옮긴다."""
    for _ in range(_MAX_CALENDAR_NAV_CLICKS):
        captions = _visible_month_captions(dialog)
        if (year, month) in captions:
            idx = captions.index((year, month))
            table = dialog.locator("table[role='grid']").nth(idx)
            table.get_by_role("button", name=f"{day}일", exact=True).click(timeout=5_000)
            return
        target_key = year * 12 + month
        min_key = min(y * 12 + m for y, m in captions)
        nav_label = "이전 달" if target_key < min_key else "다음 달"
        dialog.get_by_role("button", name=nav_label).click(timeout=3_000)
        dialog.page.wait_for_timeout(300)
    raise BaeminStatsScrapeError(f"{year}-{month:02d}-{day:02d} 날짜를 캘린더에서 찾지 못했습니다")


def _set_date_range(page, start_date: str, end_date: str) -> None:
    """열린 "기간" 다이얼로그에서 `start_date`~`end_date`("YYYY-MM-DD")를
    지정하고 적용한다. 날짜 범위 표시("~" 포함 텍스트)를 클릭해 두 달짜리
    캘린더를 연 뒤, 시작일 → 종료일 순으로 클릭하고, 캘린더 자체의 "적용"과
    상위 "기간" 다이얼로그의 "적용"을 순서대로 누른다 — 두 다이얼로그가
    겹쳐있는 동안 "적용" 버튼도 두 개 동시에 존재하므로 각 클릭을 정확히
    그 시점의 최상단 다이얼로그로 scope한다(모듈 docstring 참고)."""
    period_dialog = page.get_by_role("dialog").last
    range_display = period_dialog.get_by_text(re.compile(r"~")).first
    range_display.click(timeout=5_000)
    page.wait_for_timeout(800)

    start_y, start_m, start_d = (int(p) for p in start_date.split("-"))
    end_y, end_m, end_d = (int(p) for p in end_date.split("-"))

    cal_dialog = page.get_by_role("dialog").last
    _click_calendar_day(cal_dialog, start_y, start_m, start_d)
    page.wait_for_timeout(400)
    cal_dialog = page.get_by_role("dialog").last
    _click_calendar_day(cal_dialog, end_y, end_m, end_d)
    page.wait_for_timeout(400)

    cal_dialog = page.get_by_role("dialog").last
    cal_dialog.get_by_role("button", name="적용").first.click(timeout=5_000)
    page.wait_for_timeout(800)

    remaining = page.get_by_role("dialog").all()
    if remaining:
        outer_apply = remaining[-1].get_by_role("button", name="적용")
        if outer_apply.count() > 0:
            outer_apply.first.click(timeout=5_000)


def fetch_account_settlement(page, start_date: str, end_date: str) -> list[dict]:
    """정산내역 화면(`/orders/billing`)에서 계정 전체 입금 배치
    (settle/history/summary) organic 응답을 가로챈다. 날짜 범위를 지정한 뒤
    페이지네이션 컨트롤("더보기" 버튼, 실측 확인 — 리뷰 리스트와 동일한
    패턴)이 있으면 끝까지 반복한다."""
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
        try:
            _open_date_range_picker(page)
            _set_date_range(page, start_date, end_date)
        except PlaywrightTimeoutError as e:
            raise BaeminStatsScrapeError(f"정산내역 날짜 범위 지정에 실패했습니다: {e}") from e
        page.wait_for_timeout(2_000)

        # 90일처럼 넓은 범위에서는 페이지당 10건으로 잘려 "더보기" 버튼이
        # 나타난다(모듈 docstring 참고) — baemin_reviews.py의 "더보기" 반복
        # 클릭 + 연속 무진행 카운터 패턴을 그대로 재사용한다.
        consecutive_no_progress = 0
        for _ in range(_MAX_LOAD_MORE_CLICKS):
            more_button = page.get_by_text("더보기", exact=True)
            if more_button.count() == 0:
                break
            before = len(responses)
            if page.get_by_test_id("backdrop").count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            try:
                more_button.first.scroll_into_view_if_needed()
                more_button.first.click(timeout=5_000)
            except PlaywrightTimeoutError:
                if page.get_by_test_id("backdrop").count() > 0:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            page.wait_for_timeout(_LOAD_MORE_WAIT_MS)
            if len(responses) > before:
                consecutive_no_progress = 0
            else:
                consecutive_no_progress += 1
                if consecutive_no_progress >= _MAX_CONSECUTIVE_NO_PROGRESS:
                    break
    finally:
        page.remove_listener("response", _on_response)

    if not observed["any"]:
        raise BaeminStatsScrapeError("정산내역 API 응답을 한 번도 확인하지 못했습니다")

    return responses
