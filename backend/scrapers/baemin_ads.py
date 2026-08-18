"""배민 사장님광장의 "마케팅 성과 → 우리가게클릭" 화면(브랜드별 광고 클릭
성과) API 응답을 날짜별로 집계하는 순수 함수와, 그 organic 응답을 실제로
캡처하는 `fetch_brand_click_metrics`.

기존 `baemin_stats.py`(가게통계/정산내역/주문내역 — 계정 전체 합산 지표)와
관심사가 달라 별도 파일로 분리했다: 이 모듈이 다루는 "우리가게클릭"은
브랜드(shop_no) 단위로만 조회되는 화면이고, 계정 전체로 합산하지 않는다
(설계 문서의 "스코프 결정" 절 참고).

### collecting 게이트 (Task 3 fix round 1, 코드 리뷰 지적)

`baemin_stats.py`의 `fetch_shop_stats`/`_should_count_sales_response`와 동일한
버그 클래스가 처음 구현에서 재현됐다: `fetch_brand_click_metrics`가 화면
진입 직후(어떤 상호작용도 하기 전) 스스로 발생시키는 기본 달 응답까지
`page.on("response", ...)` 리스너가 그대로 잡아버렸다 — 실측(2026-08-12)
결과 `months=["2026-06", "2026-08"]`로 호출했을 때 기대한 2개가 아니라
3개(초기 로드분 1개 + 명시적 선택 2개)가 반환됐다. 이 초기 응답이 어느
달인지는 예측할 수 없다(계정에 persist된 마지막 조회 상태 — 오늘 날짜와
우연히 같을 뿐 보장되지 않는다, `baemin_stats.py` 모듈 docstring의
discrepancy 절과 동일한 근거). `_should_count_click_metrics_response`가
`collecting` 플래그로 이 초기 응답을 걸러낸다 — `months` 루프를 시작하기
직전에만 `True`로 바뀐다.
"""

from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


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


def _should_count_click_metrics_response(path: str, shop_no: int, collecting: bool) -> bool:
    """`/v2/statistics/campaign/cpc/metrics/{shop_no}` 응답 하나가 "엔드포인트를
    관측했다"는 신호(`observed_any`)와 실제 데이터 수집 대상으로 인정될 수
    있는지 판정하는 순수 함수(Playwright 없이 테스트 가능) — `baemin_stats.py`의
    `_should_count_sales_response`와 동일한 게이트 패턴이다.

    `collecting=False`면(아직 `months` 루프를 시작하기 전, 화면 진입 직후
    스스로 발생하는 예측 불가능한 기본 달 응답) 무조건 False다. 이 게이트가
    없으면(코드 리뷰 지적, Task 3 fix round) discard됐어야 할 그 초기 응답
    하나만으로 `observed_any`가 참이 되고, 게다가 그 응답 자체가
    `responses`에 섞여 들어가 `months`에 없는 달의 데이터가 호출자 모르게
    포함되는 문제가 생긴다 — 배민 화면이 첫 로드 시 어느 달을 기본으로
    보여줄지는 계정에 persist된 마지막 조회 상태에 달려 있어 예측할 수
    없다(`baemin_stats.py` 모듈 docstring의 discrepancy 절 참고, 오늘 날짜와
    우연히 같은 달일 뿐 보장되지 않는다)."""
    return path == f"/v2/statistics/campaign/cpc/metrics/{shop_no}" and collecting


def fetch_brand_click_metrics(page, shop_no: int, months: list[str]) -> list[dict]:
    """브랜드(shop_no)의 "마케팅 성과 → 우리가게클릭" 화면에서
    `/v2/statistics/campaign/cpc/metrics/{shopNumber}` organic 응답을
    `months`에 담긴 각 달마다 캡처한다. 계정 전체가 아니라 브랜드 하나에
    대해서만 호출한다 — 호출자가 `session.shops`를 순회하며 브랜드마다
    이 함수를 부른다.

    화면 진입 직후(어떤 상호작용도 하기 전) 스스로 발생하는 기본 달 응답은
    무시한다 — 그 응답이 어느 달인지 예측할 수 없어 `months`에 없는 달을
    끼워 넣을 위험이 있다(`_should_count_click_metrics_response` 참고,
    `baemin_stats.py`의 `_should_count_sales_response`와 동일한 게이트
    패턴). 그래서 반환되는 리스트는 성공한 달 수만큼(최대 `len(months)`개)이다
    — 응답이 200이 아니거나 JSON 파싱에 실패하는 등 개별 달 조회가 실패하면
    그보다 적을 수 있다."""
    responses: list[dict] = []
    state = {"observed_any": False, "collecting": False}

    def _on_response(response) -> None:
        url = response.url
        path = urlparse(url).path
        if "self-api.baemin.com" not in url:
            return
        if not _should_count_click_metrics_response(path, shop_no, state["collecting"]):
            return
        state["observed_any"] = True
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

        page.wait_for_timeout(4_000)  # 첫 로드(예측 불가한 기본 달) 대기
        _dismiss_backdrop_if_present(page)

        # 콘텐츠가 스켈레톤 상태로 몇 초간 유지될 수 있어, "N월" 라벨이
        # 실제로 나타날 때까지 최대 15초 폴링한다(고정 대기만으로는 첫
        # 로드 시점에 따라 부족할 수 있음을 실측으로 확인했다).
        import re
        for _ in range(15):
            if page.get_by_text(re.compile(r"^\d+월$"), exact=True).count() > 0:
                break
            page.wait_for_timeout(1_000)

        # 여기까지는 화면이 스스로 발생시킨(예측 불가한 기본 달) 응답만
        # 있을 수 있다 — 이제부터 명시적으로 월을 선택하기 시작하므로 그
        # 응답부터 실제 수집 대상으로 인정한다(위 docstring, 코드 리뷰
        # 지적사항 fix round 참고).
        state["collecting"] = True

        for month in months:
            try:
                _select_click_metrics_month(page, month)
            except PlaywrightTimeoutError as e:
                raise BaeminAdsScrapeError(f"{month} 우리가게클릭 성과 조회 중 월 선택에 실패했습니다: {e}") from e
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminAdsScrapeError("우리가게클릭 성과 API 응답을 한 번도 확인하지 못했습니다")

    return responses


def fetch_cpc_booking(page, shop_no: str) -> dict:
    """사장님광장 "광고·서비스관리" 화면(`/shops/{shop_no}/ad/campaign`)에서
    `GET /v4/cpc/bookings/by-shop-number?shopNumber={shop_no}` organic 응답을
    가로채 현재 CPC 입찰가 등을 반환한다. `fetch_shop_info`(baemin_stats.py)와
    동일한 단발성 GET 인터셉트 패턴 — 화면 진입만으로 호출되는 API라
    `fetch_brand_click_metrics`처럼 명시적 상호작용을 기다릴 필요가 없다.

    반환 키: `bid`(int, 클릭당 희망 광고금액=현재 CPC), `max_bid`(int),
    `monthly_budget`(int), `spent_budget`(int), `is_auto_bidding`(bool).
    """
    state = {"observed_any": False, "body": None}

    def _on_response(response) -> None:
        url = response.url
        if "self-api.baemin.com" not in url:
            return
        if urlparse(url).path != "/v4/cpc/bookings/by-shop-number":
            return
        state["observed_any"] = True
        if response.status == 200:
            try:
                state["body"] = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/ad/campaign")
        except Exception as e:
            raise BaeminAdsScrapeError(f"광고·서비스관리 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)
        _dismiss_backdrop_if_present(page)
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 한 번도 확인하지 못했습니다")
    if state["body"] is None:
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 받았지만 파싱하지 못했습니다")

    body = state["body"]
    try:
        return {
            "bid": int(body["bid"]),
            "max_bid": int(body["maxBid"]),
            "monthly_budget": int(body["monthlyBudget"]),
            "spent_budget": int(body["spentBudget"]),
            "is_auto_bidding": bool(body["isAutoBidding"]),
        }
    except (KeyError, TypeError) as e:
        raise BaeminAdsScrapeError(f"CPC 입찰가 응답 형태가 예상과 다릅니다: {e}") from e
