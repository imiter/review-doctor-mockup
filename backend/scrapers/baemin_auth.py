"""Playwright로 배민 사장님광장(self.baemin.com)에 실제 로그인해 인증된 세션을 만든다.

로그인 성공 후 계정에 연결된 첫 번째 매장의 shopNo도 함께 확인해 반환한다(여러
매장이 있어도 매장 선택 UI는 만들지 않는다 — 범위 밖). 로그인 폼의 선택자와
매장 목록 확인 방식은 추측하지 않고 실제 화면에서 `playwright codegen`으로
확인한 값을 쓴다.

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
    request_context: object
    shop_no: int
    shop_name: str
    _playwright: object
    _browser: object

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()


def _extract_login_error(page) -> str | None:
    for selector in ("[role='alert']", ".error-message"):
        el = page.query_selector(selector)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    return None


def _discover_first_shop(page) -> tuple[int, str]:
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
    for option in options:
        value = option.get_attribute("value")
        if not value or not value.isdigit():
            continue  # "선택하세요" 같은 플레이스홀더 옵션은 건너뛴다
        return int(value), option.inner_text().strip()
    raise BaeminLoginError("매장 목록을 확인하지 못했습니다")


def login(login_id: str, password: str, headless: bool = True) -> BaeminSession:
    playwright = sync_playwright().start()
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

    # 아래 try 블록에서 나는 실패는 BaeminLoginError만이 아니다 — page.goto의
    # 네트워크/타임아웃 오류, get_by_test_id("id")/("password")/로그인 버튼의
    # 기본 30초 타임아웃, _discover_first_shop의 리뷰관리 클릭·option 대기
    # 타임아웃 등도 전부 여기서 날 수 있다. 예전에는 `except BaeminLoginError`만
    # 정리를 했어서 그 외 예외는 브라우저/Playwright 드라이버 프로세스를 그대로
    # 흘려보냈다(실제로 봉 탐지 차단으로 인한 fill() 타임아웃에서 재현됨).
    # success 플래그 + finally로 성공 여부와 무관하게 항상 정리하고, 호출자가
    # BaeminLoginError 하나만 잡으면 되도록 다른 예외는 여기서 감싸 다시 던진다.
    success = False
    try:
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

        shop_no, shop_name = _discover_first_shop(page)

        session = BaeminSession(
            request_context=context.request,
            shop_no=shop_no,
            shop_name=shop_name,
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
            browser.close()
            playwright.stop()
