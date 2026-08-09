"""Playwright로 배민 사장님광장(self.baemin.com)에 실제 로그인해 인증된 세션을 만든다.

로그인 성공 후 계정에 연결된 매장을 전부(하나의 로그인에 여러 브랜드/매장이
딸린 계정도 있다 — 실 계정 스크린샷으로 확인) 확인해 `BaeminSession.shops`로
반환한다. `review_sync.py`의 리뷰 동기화 단계는 이 리스트를 순회하며 매장마다
`fetch_all_reviews`를 호출해 계정에 딸린 모든 브랜드의 리뷰를 동기화한다.
`shop_no`/`shop_name` 필드는 하위 호환을 위해 첫 번째 매장 값을 그대로 남겨둔다
— 예: 최초 배민 로그인 시 연결(`store_platform_connections`)의
`platform_store_id`로 저장되는 대표 매장 표시용. 로그인 폼의 선택자와 매장 목록
확인 방식은 추측하지 않고 실제 화면에서 `playwright codegen`으로 확인한 값을
쓴다.

확인된 동작:
- `https://self.baemin.com/login`으로 바로 이동하면 "페이지를 찾을 수 없음" 에러
  화면이 먼저 뜨고, "홈으로 이동" 버튼을 눌러야 실제 로그인 폼이 나온다(새
  브라우저 컨텍스트에서도 매번 재현되는 동작 — 쿠키 문제가 아니라 항상 필요한
  복구 단계).
- 로그인 성공 후 "리뷰관리 리뷰관리" 버튼을 누르면 매장 선택용 네이티브
  `<select>`가 나타난다(페이지의 두 번째 combobox). 이 select의 `<option>`
  value가 그대로 shopNo이고 텍스트가 매장명이다. 네트워크 응답을 가로채는 방식
  대신 이 DOM을 직접 읽는다.
- 기본 설정(자동화 흔적이 남는 headless Chromium)으로 접속하면 배민의 봉 탐지가
  "비정상 동작이 감지되어 잠시 이용이 제한돼요" 차단 화면을 띄워서 "홈으로
  이동" 클릭 후에도 실제 로그인 폼(biz-member.baemin.com)에 도달하지 못한다.
  실제 계정으로 재현 확인한 결과, `--disable-blink-features=AutomationControlled`
  실행 인자와 `navigator.webdriver` 오버라이드, 일반적인 데스크톱 Chrome
  User-Agent/뷰포트만 추가하면(번들 Chromium 그대로, 별도 실브라우저 채널 불필요)
  차단 없이 정상적으로 로그인 폼까지 도달한다.
- 로그인 성공 직후 대시보드 홈에 "스마트 모드로 효과를 높여요!" 같은 프로모션
  모달이 backdrop과 함께 뜰 때가 있다(매번 재현되지는 않아 세션/일자에 따라
  달라지는 것으로 보인다). 이 backdrop이 클릭을 가로채 이후 "리뷰관리" 클릭이
  타임아웃 나므로, `_discover_all_shops` 호출 전에 방어적으로 Escape 키를
  눌러 닫는다(실 계정으로 재현·해결 확인).
- 리뷰 API(self-api.baemin.com)를 `context.request`(브라우저 밖 `APIRequestContext`)로
  직접 호출하면 쿠키가 실려도 HTTP 403이 난다(실 계정으로 재현 확인). 실제
  요청에는 매 요청마다 값이 바뀌는 `x-e-request` 서명 헤더가 실려 있는데, 이는
  배민 프런트 JS가 동적으로 생성하는 값이라 정적으로 재현할 수 없다.
  `page.evaluate()`로 페이지 안에서 raw `fetch()`를 직접 실행해도 이 서명
  로직을 우회하게 돼 CORS로 차단된다(실 계정 콘솔 캡처로 재현 확인 —
  `baemin_reviews.py` 모듈 docstring 참고). 그래서 로그인 성공 후에도 `page`를
  닫지 않고 세션에 그대로 담아 반환한다 — `baemin_reviews.py`의 리뷰 조회는
  이 살아있는 페이지가 "리뷰관리" 화면을 로드하며 스스로 발생시키는(organic)
  올바르게 서명된 네트워크 응답을 `page.on("response", ...)`로 가로채는
  방식으로 동작한다.
"""

from dataclasses import dataclass

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_LOGIN_URL = "https://self.baemin.com/login"


class BaeminLoginError(Exception):
    pass


@dataclass
class BaeminSession:
    page: object
    shop_no: int
    shop_name: str
    shops: list[tuple[int, str]]
    _playwright: object
    _browser: object

    def close(self) -> None:
        # browser.close()가 실패해도(예: 배민 봉 탐지로 브라우저 프로세스가 이미
        # 죽은 경우) playwright.stop()은 항상 실행되어야 드라이버 프로세스가
        # 남지 않는다.
        try:
            self._browser.close()
        finally:
            self._playwright.stop()


def _extract_login_error(page) -> str | None:
    for selector in ("[role='alert']", ".error-message"):
        el = page.query_selector(selector)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    return None


def _discover_all_shops(page) -> list[tuple[int, str]]:
    page.get_by_role("button", name="리뷰관리 리뷰관리").click()
    shop_select = page.get_by_role("combobox").nth(1)
    # click()은 버튼 자체의 등장만 기다린다 — 그 뒤에 렌더링되는 select의
    # <option>들은 별도로 기다려야 한다. Locator.all()은 auto-wait를 하지 않고
    # 호출 시점에 DOM에 있는 것만 즉시 읽으므로, 기다리지 않으면 아직 옵션이
    # 그려지기 전에 빈 리스트를 읽어 "매장 목록을 확인하지 못했습니다"라는
    # 잘못된 에러(실제로는 타이밍 문제일 뿐)를 내게 된다. state="attached"만
    # 확인하는 이유는 네이티브 select의 option은 Playwright의 "visible" 판정이
    # 불안정하기 때문 — DOM에 붙었는지만 확인하면 충분하다.
    shop_select.locator("option").first.wait_for(state="attached")
    options = shop_select.locator("option").all()
    shops: list[tuple[int, str]] = []
    for option in options:
        value = option.get_attribute("value")
        if not value or not value.isdigit():
            continue  # "선택하세요" 같은 플레이스홀더 옵션은 건너뛴다
        shops.append((int(value), option.inner_text().strip()))
    if not shops:
        raise BaeminLoginError("매장 목록을 확인하지 못했습니다")
    return shops


def login(login_id: str, password: str, headless: bool = True) -> BaeminSession:
    # success 플래그 + finally로 성공 여부와 무관하게 항상 정리한다. 이 스코프는
    # playwright.start() 직후부터 시작해 browser/context/page 생성 자체의 실패까지
    # 덮는다 — 예전에는 이 생성 단계가 try 블록 밖에 있어서, 예를 들어
    # chromium.launch()가 실패하면(배포 환경에 브라우저 바이너리가 없는 경우 등)
    # Playwright 드라이버 프로세스가 정리 없이 그대로 흘렀다. browser는 launch()
    # 자체가 실패하면 아직 없을 수 있으므로 None으로 초기화해 finally에서 가드한다.
    playwright = sync_playwright().start()
    browser = None
    success = False
    try:
        browser = playwright.chromium.launch(
            headless=headless,
            # 자동화 흔적이 남는 기본 headless 설정은 배민의 봉 탐지에 걸려
            # "비정상 동작이 감지되어 잠시 이용이 제한돼요" 차단 화면으로 리다이렉트된다
            # (실 계정으로 재현 확인). 아래 인자와 context 설정이 그 우회다.
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=_DESKTOP_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # 아래에서 나는 실패는 BaeminLoginError만이 아니다 — page.goto의
        # 네트워크/타임아웃 오류, get_by_test_id("id")/("password")/로그인 버튼의
        # 기본 30초 타임아웃, _discover_first_shop의 리뷰관리 클릭·option 대기
        # 타임아웃 등도 전부 여기서 날 수 있다. 예전에는 `except BaeminLoginError`만
        # 정리를 했어서 그 외 예외는 브라우저/Playwright 드라이버 프로세스를 그대로
        # 흘려보냈다(실제로 봉 탐지 차단으로 인한 fill() 타임아웃에서 재현됨).
        # 호출자가 BaeminLoginError 하나만 잡으면 되도록 다른 예외는 여기서 감싸
        # 다시 던진다.
        page.goto(_LOGIN_URL)

        try:
            # /login에 바로 접근하면 "페이지를 찾을 수 없음" 화면이 먼저 뜬다 —
            # 실제 로그인 폼으로 가려면 이 복구 클릭이 항상 필요하다. 동작이
            # 바뀌어 버튼이 없어지는 경우를 대비해 짧은 타임아웃으로 방어한다.
            page.get_by_role("button", name="홈으로 이동").click(timeout=3_000)
        except PlaywrightTimeoutError:
            pass

        page.get_by_test_id("id").fill(login_id)
        page.get_by_test_id("password").fill(password)
        page.get_by_role("button", name="로그인").click()

        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
        except Exception as e:
            error_text = _extract_login_error(page)
            raise BaeminLoginError(error_text or "로그인에 실패했습니다. 잠시 후 다시 시도해주세요") from e

        # 로그인 직후 대시보드 홈에 프로모션 모달(backdrop 포함)이 뜰 때가 있어
        # 이후 "리뷰관리" 클릭이 막힌다. 모달이 없어도 Escape는 아무 영향이 없으므로
        # 항상 방어적으로 눌러준다.
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        shops = _discover_all_shops(page)
        shop_no, shop_name = shops[0]

        session = BaeminSession(
            page=page,
            shop_no=shop_no,
            shop_name=shop_name,
            shops=shops,
            _playwright=playwright,
            _browser=browser,
        )
        success = True
        return session
    except BaeminLoginError:
        raise
    except Exception as e:
        raise BaeminLoginError(f"로그인 처리 중 예기치 못한 오류가 발생했습니다: {e}") from e
    finally:
        if not success:
            if browser is not None:
                browser.close()
            playwright.stop()
