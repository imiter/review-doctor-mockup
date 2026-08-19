"""배민 사장님광장의 가게통계(매출/재주문율)·정산내역(입금)·주문내역(이번
달 보완 매출) API 응답을 날짜별로 집계하는 순수 함수와, 그 organic 응답을
실제로 캡처하는 `fetch_shop_stats`/`fetch_account_settlement`/
`fetch_orders`.

브랜드(치밥대장 등)별로 분리하지 않고 전부 날짜 단위로 합산한다 — 설계
결정(계정 전체 합산만 지원, 입금이 애초에 브랜드별로 안 나뉘어 나오기
때문). 매핑 함수는 모두 여러 브랜드/여러 달/여러 페이지에 걸친 raw 응답
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

**정산내역 페이지네이션 (정정, 2026-08-11 세션 중 재현·수정)**: 원래 "리뷰
리스트와 동일한 '더보기' 패턴"이라고 서술했었으나 틀렸다 — `_click_load_more_until_done`이
찾는 "더보기" 텍스트가 정산 목록 자체가 아니라 화면 하단의 무관한 "전문가
Q&A" 섹션의 "더보기 ›" 링크와 우연히 매칭돼, 그 링크를 계속 클릭하면서도
정산 목록에는 새 요청이 전혀 발생하지 않았다(재현: `totalSize=20`인데
매번 첫 페이지 10건만 수집됨). 실제로는 주문내역과 마찬가지로 숫자
페이지네이션(`1 2 3 ...` + 접근성 이름 `"다음"`인 다음-페이지 버튼)이라
`fetch_account_settlement`는 `_click_next_page_until_done`을 쓴다(아래
"정정 (2026-08-12)" 절의 주문내역 발견과 같은 종류의 문제 — 이 모듈
안에서 화면마다 페이지네이션 방식이 다르다는 걸 매번 실측으로 확인해야
한다는 교훈이 두 번째로 나온 셈이다). 페이지 경계에서 같은 배치(`giveId`)가
두 페이지에 겹쳐 나타날 수 있어 `map_deposits_by_date`가 `giveId` 기준
dedupe를 한다. 날짜 범위를 지정하기 전 페이지 최초 로드 시점에도 화면이
스스로 기본 필터에 대한 organic 응답을 한 번 발생시키므로(`fetch_shop_stats`의
`collect_sales`와 동일한 문제), `collecting` 플래그로 걸러낸다.

**정산 상세 카드 클릭(`fetch_settlement_breakdown_details`) — 정정,
2026-08-12 Task 3 실 계정 검증 중 발견**: 브리프의 스타팅 코드는 카드
"하나" 클릭만 검증됐고 여러 카드 연속 클릭 + 페이지네이션은 미검증이었는데,
실제로 돌려보니 순서대로 세 가지 버그가 나왔다 — (1) 상태 배지
`has_text=re.compile(r"^입금(완료|예정)$")` 필터가 앵커 때문에 상수적으로
빈 로케이터였던 것(카드 컨테이너 텍스트가 배지 하나만으로 이루어진 적이
없어서), (2) 카드 클릭이 인라인 펼치기가 아니라 별도 모달을 열어 안 닫고
다음 카드를 클릭하면 그 클릭이 모달 backdrop에 흡수되는 것, (3) 로그인
기본 뷰포트(1280×800)로는 8번째 카드부터 화면 밖이라 클릭이 무효고,
`scroll_into_view_if_needed`로 보완하면 화면 우하단 고정 AI 챗봇 플로팅
버튼과 카드 "오른쪽 끝" 클릭 좌표가 겹쳐 그 챗봇이 열리며 이후 클릭이
전부 막히는 것. 세 가지 모두 `_click_all_settlement_cards_on_page`
docstring의 "정정"/"정정 2" 절에 상세 재현 경위가 있다. 최종적으로는
모달을 매 클릭 뒤 Escape로 닫고, 카드 클릭 루프 동안만 뷰포트를 3000px로
늘려 스크롤 자체를 없애는 방식으로 해결했다 — 실 계정 30일 창에서 카드
20개(2페이지) 전부 캡처하는 것까지 확인했다.

**주문내역(`/orders/history`)은 정산내역과 완전히 같은 날짜 범위 다이얼로그
컴포넌트를 쓴다**(실측 확인, 2026-08-11 fix round) — "날짜 직접 선택" 버튼
텍스트, "기간" 다이얼로그 구조, 두 달짜리 캘린더 그리드까지 전부 동일해서
`_open_date_range_picker`/`_set_date_range`를 그대로 재사용할 수 있었다.
차이는 안내 문구(정산내역 "최근 5년 동안의 정산내역만", 주문내역 "최근
5년 동안 받은 주문을 볼 수 있어요" + "한번에 6개월까지 조회할 수 있어요")뿐.
`GET /v4/orders?...&shopNumbers=&orderStatus=CLOSED`처럼 `shopNumbers`가
빈 채로 나가는 것도 실측 확인했다 — 계정에 연결된 모든 브랜드를 한 번의
조회로 함께 반환하므로, `fetch_shop_stats`처럼 shop_no별로 반복할 필요가
없다.

**정정 (2026-08-12, 실 계정 로컬 검증 중 발견) — 주문내역은 "더보기"가
아니라 숫자 페이지네이션을 쓴다.** 위 "정산내역/주문내역 페이지네이션이
동일하다"는 원래 서술은 틀렸다 — 실제 배포 전 로컬에서 실 계정으로 한
달치(196건) 매출을 동기화해보니 대시보드 매출이 배민 사이트에서 직접 확인한
값의 일부(10건, `limit=10&offset=0`)만 반영돼 있었다. 재조사 결과 주문내역
화면에는 "더보기" 텍스트 버튼이 아예 없고(실측: `page.get_by_text("더보기",
exact=True).count() == 0`, 스크롤을 여러 번 반복해도 추가 요청이 안
나감), 대신 리스트 하단에 숫자 페이지네이션(`1 2 3 ... 20` + 접근성 이름
`"다음"`인 다음-페이지 버튼)이 있다. `"다음"` 버튼을 클릭할 때마다
`offset`이 10씩 늘어난 `/v4/orders` 요청이 새로 나가고, 마지막 페이지에서는
버튼이 비활성화돼 클릭이 타임아웃 난다(실측: 20페이지·196건을 이 방식으로
끝까지 수집해 8/1~8/11 전 구간이 채워지는 것까지 확인). `_click_load_more_until_done`은
계속 `fetch_account_settlement`(정산내역, 실제로 "더보기"를 쓰는 화면)
전용으로 남겨두고, `fetch_orders`는 별도의
`_click_next_page_until_done`(숫자 페이지네이션 + 타임아웃 기반 종료)을
쓰도록 고쳤다.

### 정정 (2026-08-13, 최종 리뷰 fix round) — 주문내역 수집의 "조용한 절단" 세 원인

3개월 백필을 요청했는데 6일치 120건만 저장되고 job은 `success`로 끝난 사고를
실 계정으로 재현·분해한 결과, 원인이 세 개였다. 셋 다 "일부만 가져오고 성공한
척"으로 끝나는 종류였고, 증분 커서(`MAX(ordered_at)`)가 그 위에서 오늘까지
전진해버리기 때문에 못 가져온 구간이 영구 유실됐다.

1. **날짜 범위가 조용히 다르게 적용된다.** "기간" 다이얼로그의 캘린더는
   상태 기계라, 같은 "시작일 클릭 → 종료일 클릭"이 진입 시점의 기존 선택에
   따라 전혀 다른 결과를 낸다(상세 표는 `_set_date_range` docstring). 실측:
   `2026-08-08~2026-08-13`을 요청했는데 실제 적용은 `2026-08-13~2026-08-13`
   (하루)이었다. 기존 코드는 적용 결과를 확인하지 않았다.
2. **범위 밖 응답이 그대로 섞여 들어온다.** 주문내역 화면은 진입 즉시 기본
   필터(최근 7일)로 organic 요청을 한 번 보낸다(실측:
   `startDate=2026-08-07&endDate=2026-08-13`, `totalSize=126`). `fetch_orders`에는
   `fetch_shop_stats`/`fetch_account_settlement`가 쓰는 `collecting` 게이트가
   없어서 이 응답을 그대로 수집했다 — 위 1번과 겹치면 "요청한 3개월" 대신
   기본 7일치를 반환하면서도 호출자는 알 방법이 없다(사고 당시 저장된
   "6일치 120건"의 정체가 정확히 이것이다).
3. **페이지네이션이 조용히 일찍 끝난다.** 상한 30회(=300건) 고정이라 3개월
   (실측 1,541건 = 155페이지)은 애초에 도달 불가였고, 클릭 타임아웃을
   버튼 비활성(진짜 마지막 페이지)과 구분하지 않았으며, 클릭 후 1.5초 안에
   응답이 안 오면 마지막 페이지로 오판했다(실측 재현).

수정 방향은 "조용한 절단을 구조적으로 불가능하게 만든다"다. `/v4/orders`
응답 최상위에 `{"totalSize": int, "totalPayAmount": int, "contents": [...]}`가
있는 걸 확인해(정산 summary의 `totalSize`와 같은 종류의 신호) 이걸 완결성
기준으로 삼는다 — 요청한 범위와 정확히 일치하는 응답만 수집하고, 수집
건수가 `totalSize`에 못 미치면 하드 에러를 던진다. 자세한 근거는
`fetch_orders`/`_click_next_page_until_done`/`_set_date_range` docstring 참고.

**오버레이가 두 종류라는 것도 이때 확인했다.** `data-testid="backdrop"`
(다이얼로그/프로모션, Escape로 닫힘) 말고도
`div[class*="LoadingBackdrop"]`(데이터 로딩 중, Escape로 안 닫힘)가 따로 있고,
둘 다 클릭을 가로챈다(실측 로그: `<div class="LoadingBackdrop-module__...">
intercepts pointer events`). 로딩 오버레이는 "기다리는" 게 유일한 대응이라
`_wait_for_loading_overlay`/`_clear_click_blockers`로 나눠 처리한다.

### crmInfo 재조사 결과 (fix round, 2026-08-11)

Task 2 최초 구현에서는 crmInfo가 두 차례 재현 모두 0건이라 미관측을
비하드에러로 감내했었다. 사용자가 실제 화면에서 "신규·재주문" 위젯이
분명히 데이터를 보여주는 걸 직접 확인해 재조사를 요청했고, 재조사 결과는
"원인 불명"이 아니라 실제로 찾아 고칠 수 있는 버그와, 고쳐도 남는
잔여 flakiness 두 가지로 나뉜다.

**찾아서 고친 진짜 버그**: 원래 구현은 "신규-재주문"(하이픈)으로 헤딩을
찾아 그 위치로 스크롤을 시도했는데, 실제 DOM 텍스트는 "신규·재주문"
(가운뎃점 U+00B7 — 이 사이트가 "일・주" 탭처럼 구분자로 흔히 쓰는 문자)
이라 검색이 매번 실패했고, 매칭 실패 시 쓰던 fallback(고정 1200px 스크롤)이
위젯을 정확히 뷰포트 안에 넣지 못해 지연 로드가 트리거되지 않았다 —
그래서 원래 두 차례 재현 모두 0건이었다. 구분자 문자에 의존하지 않는
"재주문" 부분 문자열 검색 + 정확한 bounding box 계산 후 뷰포트 세로
중앙으로 스크롤하는 방식으로 바꾼 뒤, **격리된 진단 스크립트로 두 차례
재현했을 때 2초 만에 크린하게 잡히는 걸 확인했다**(이 텍스트 매칭 버그가
"원인 불명"이 아니라 진짜 원인이었다는 확증).

**같은 수정을 `fetch_shop_stats` 안에서 실행했을 때 남는 잔여
flakiness**: 위 수정된 스크롤 타겟팅 로직을 그대로 `fetch_shop_stats`에
반영하고, 헤딩 렌더 대기 재시도(최대 5초)·다단계 폴링(최대 12초)·재트리거용
스크롤 지글(scroll away then back)까지 추가로 붙였는데도, `fetch_shop_stats`를
실제로 호출한 4번의 시도 중 2번은 여전히 crmInfo가 안 잡혔다(격리
진단에서는 2번 다 성공). 동일 세션의 매출/정산/주문내역 엔드포인트는 이
fix round 동안 단 한 번도 놓친 적이 없다는 것과 대비된다 — "신규·재주문"
위젯 자체의 지연 로드 타이밍이 클라이언트 쪽에서 아무리 정밀하게
스크롤해도 완전히 통제되지 않는, Baemin 쪽의 잔여 flakiness로 보인다.
그래서 `fetch_shop_stats`는 crmInfo 미관측을 여전히 하드 에러로 취급하지
않는다(매출/정산/주문내역과 다른 엄격도) — 다만 이제는 "원인 불명이라
포기"가 아니라 "근본 원인 하나는 찾아 고쳤고, 남은 부분은 재현 가능한
확률적 flakiness"라는 근거가 있다.

**부수 발견 — 관련 있지만 대체재는 아닌 엔드포인트**: 재조사 도중
"신규·재주문" 위젯의 스켈레톤 DOM 클래스가 `NewReOrderCardSkeleton-module__...`
인 걸 보고, 실제로 그 이름과 일치하는 `GET /v3/statistics/new-reorder/summary
?shopNumber={shop_no}&period=MONTH&month=YYYY-MM` 요청이 (crmInfo와는 별개로)
관측됐다. 그런데 이 응답의 실제 형태는
`{"newOrderCount": int, "reOrderCount": int, "details": [{"group": "ALL_ORDER"|"INSTANT_DISCOUNT"|"BAEMIN_CLUB"|"MFO"|"TAKEOUT", "orderCount": int, "newOrderCount": int, "reOrderCount": int}, ...]}`
로, 날짜별 데이터가 전혀 없고(그 달 전체의 단일 합계를 주문 채널별로만
나눔) `map_repurchase_by_date`가 기대하는
`newReorderSummary.timeNewGraph`/`timeReorderGraph`(날짜별 x/y 포인트) 형태와
완전히 다르다 — `repurchase_metrics`가 요구하는 날짜별 재주문율(위 "정규화
원칙"/테이블 설명 참고)과 근본적으로 호환되지 않는다. 그래서 이번
fix round에서는 이 엔드포인트로 갈아타지 않았다 — crmInfo가 그대로
목표로 남아있고, 이 엔드포인트는 "왜 crmInfo가 아닌 다른 요청도 같이
관측되는지"를 설명하는 참고 정보로만 문서에 남긴다. 날짜별 재주문율을
이 엔드포인트 기반으로 다시 설계하려면 스키마/집계 로직 자체를 다시
논의해야 한다(이 fix round의 범위 밖).

**backdrop 처리 시 주의**: `data-testid="backdrop"`은 프로모션 모달
전용이 아니라, 배민 자체 디자인시스템의 모든 다이얼로그(기간 모달, 월
선택, 날짜 캘린더 등)가 열려있는 동안 공통으로 갖는 레이어다. 그래서
`_dismiss_backdrop_if_present`는 반드시 "우리 자신의 다이얼로그를 열기
전"(페이지 진입 직후, 또는 한 달/한 페이지 조회가 완전히 끝나고 다음
반복을 시작하기 전)에만 호출해야 한다 — 다이얼로그를 여는 클릭들
사이사이에 방어적으로 호출하면 방금 우리가 연 다이얼로그 자체를
Escape로 닫아버린다(실 계정 재현으로 확인된 버그 패턴).
"""

import calendar
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scrapers.baemin_auth import capture_failure_diagnostics

_MAX_LOAD_MORE_CLICKS = 30
_MAX_CONSECUTIVE_NO_PROGRESS = 2
_LOAD_MORE_WAIT_MS = 1_500
_MAX_CALENDAR_NAV_CLICKS = 36
# 주문내역 페이지네이션 한 페이지 크기(실측: `limit=10&offset=0`부터 10씩 증가).
_ORDER_PAGE_SIZE = 10
# 주문내역 기본 페이지 클릭 상한. `fetch_orders`의 기본 용도(이번 달 1일~오늘
# 매출 보완)를 기준으로 잡는다 — 실측(2026-08-13) 이 계정의 주문량은 하루
# 약 17건(3개월 1,541건 / 92일, 최근 7일 126건 / 7일)이라 한 달이면 약 520건
# = 52페이지다. 기존 상한 30(=300건)은 매달 중순 이후 이번 달 매출을 조용히
# 잘라내고 있었다(이번 fix에서 실측으로 확인) — 두 배 이상 여유를 둔다.
_MAX_ORDER_PAGE_CLICKS = 120
# 3개월 백필 전용 상한. 실측(2026-08-13) 3개월 창의 `totalSize`가 1,541건
# = 155페이지였다 — 주문량이 지금의 2.5배가 되어도 견디도록 잡는다. 이
# 값이 모자라면 조용히 잘리는 게 아니라 아래 `fetch_orders`의 totalSize
# 대조가 하드 에러를 던진다(설계상 "조용한 절단"은 발생하지 않는다).
ORDER_BACKFILL_PAGE_CLICKS = 400
# "다음" 클릭 후 새 페이지가 도착하길 기다리는 최대 시간. 고정 대기
# (`_LOAD_MORE_WAIT_MS`) 하나로는 부족했다 — 넓은 날짜 범위를 적용한 직후
# 첫 페이지 전환은 실측에서 1.5초 안에 안 끝나는 경우가 있었고, 그때
# 기존 코드는 "진행 없음 = 마지막 페이지"로 오판하고 조용히 멈췄다.
_NEXT_PAGE_PROGRESS_TIMEOUT_MS = 15_000
_NEXT_PAGE_POLL_MS = 500
# 클릭이 오버레이(로딩 backdrop/다이얼로그 backdrop)에 가로채였을 때 같은
# 페이지를 다시 시도하는 횟수.
_NEXT_PAGE_BLOCKED_RETRIES = 3
# 날짜 범위 선택이 요청한 대로 적용됐는지 확인하고 다시 시도하는 횟수
# (아래 `_set_date_range` docstring의 캘린더 상태 기계 참고 — 실측상 2회면
# 항상 수렴했다).
_MAX_DATE_RANGE_ATTEMPTS = 3
_APPLIED_RANGE_RE = re.compile(r"\d+")
_MONTH_CAPTION_RE = re.compile(r"(\d{4})년 (\d{1,2})월")
_CARD_DATE_RE = re.compile(r"^\d{1,2}월 \d{1,2}일$")
# 정산 상세 카드 클릭 루프 동안만 쓰는 임시 뷰포트 높이. 기본 로그인 뷰포트
# (1280x800, baemin_auth.py)로는 카드 8번째부터 화면 밖으로 밀려나 클릭이
# 무효화되고, 스크롤로 보완하면 화면 우하단에 고정된 AI 챗봇 플로팅 버튼과
# 카드의 "오른쪽 끝" 클릭 좌표가 겹쳐버린다(실측 확인,
# `_click_all_settlement_cards_on_page` docstring "정정 2" 참고) — 카드를
# 전부 스크롤 없이 한 화면에 담을 만큼 넉넉하게 키워 두 문제를 동시에
# 피한다.
_CARD_CLICK_VIEWPORT_HEIGHT = 3000


class BaeminStatsScrapeError(Exception):
    pass


# 배민이 내려주는 시각 문자열(`orderDateTime` 등)은 타임존 오프셋이 없는
# 한국 현지 벽시계 시간이다(실측: `"2026-08-13T02:19:37"`). 이 값을 그대로
# naive datetime으로 `TIMESTAMPTZ` 컬럼에 넣으면 Postgres가 세션 타임존
# (배포 환경 기준 UTC)으로 해석해 실제보다 9시간 늦은 순간으로 저장한다 —
# 15시 이후 주문은 날짜까지 하루 밀린다. 그래서 DB에 넣기 전에 이 타임존을
# 명시적으로 붙인다(2026-08-13 fix).
KST = ZoneInfo("Asia/Seoul")


def parse_baemin_datetime(value: str) -> datetime:
    """배민이 준 시각 문자열을 **타임존이 붙은** datetime으로 변환한다.

    배민의 `orderDateTime`은 `"2026-08-13T02:19:37"`처럼 오프셋이 없는 한국
    현지 벽시계 시간이다(실측). 이 값을 naive 그대로 `TIMESTAMPTZ` 컬럼에
    넣으면 Postgres가 세션 타임존(배포 환경 기준 UTC)으로 해석해 실제보다
    9시간 늦은 순간으로 저장한다 — 19~02시(치킨 배달의 정상적인 저녁·심야
    피크)가 화면에 04~11시로 찍히고, 15시 이후 주문(실측 데이터의 91%)은
    날짜까지 하루 밀린다(2026-08-13 최종 리뷰에서 실데이터로 확인).

    같은 동기화 안에서 `map_orders_to_daily_sales`는 같은 `orderDateTime`의
    앞 10글자를 한국 날짜로 그대로 쓴다 — 변환을 안 하면 하나의 원본 필드가
    두 개의 서로 다른 타임존 가정으로 저장되는 모순이 생긴다.

    이미 오프셋이 붙어 있는 값은 그대로 존중한다(절대 시각이 이미 확정된
    값에 KST를 덮어쓰면 오히려 틀린다)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=KST)


def _dismiss_backdrop_if_present(page) -> None:
    # baemin_reviews.py의 페이지네이션 클릭과 동일한 방어 패턴 — 프로모션
    # 모달이 조사 도중 언제든 다시 뜰 수 있다(실 계정으로 확인됨). 단,
    # 우리 자신의 다이얼로그가 열려있는 도중에는 호출하지 않는다(모듈
    # docstring의 "backdrop 처리 시 주의" 참고).
    if page.get_by_test_id("backdrop").count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def _loading_overlay_visible(page) -> bool:
    """로딩 오버레이가 **지금 실제로 화면을 덮고 있는지** 판정한다.

    반드시 `:visible`로 봐야 한다(2026-08-13 실측): 이 오버레이 엘리먼트는
    로딩 중이 아닐 때도 DOM에 그대로 남아 있어(`count() == 1`) 존재 여부로
    판정하면 "영원히 로딩 중"으로 오해한다. 그러면 아래 대기 함수가 매번
    타임아웃을 통째로 소모해 페이지네이션이 페이지당 10.7초까지 느려진다
    (3개월 155페이지면 28분 — 실측으로 확인하고 고친 버그다)."""
    return page.locator("div[class*='LoadingBackdrop']:visible").count() > 0


def _wait_for_loading_overlay(page, timeout_ms: int = 10_000) -> bool:
    """로딩 오버레이가 사라질 때까지 기다린다. 사라졌으면(또는 애초에 안
    보였으면) True.

    `data-testid="backdrop"`(다이얼로그 backdrop, Escape로 닫힘)과는 **다른**
    레이어다(2026-08-13 실측으로 새로 발견). 이 레이어는 화면이 데이터를
    불러오는 동안만 보이고 Escape로 닫히지 않으며, 떠 있는 동안의 클릭은
    전부 이 레이어가 가로챈다 — 실 계정 재현에서 "날짜 직접 선택" 클릭이
    `<div class="LoadingBackdrop-module__...">intercepts pointer events`로
    타임아웃 나는 것을 직접 확인했다. 그래서 Escape가 아니라 "기다린다"가
    유일한 올바른 대응이다."""
    for _ in range(max(1, timeout_ms // 250)):
        if not _loading_overlay_visible(page):
            return True
        page.wait_for_timeout(250)
    return not _loading_overlay_visible(page)


def _clear_click_blockers(page, timeout_ms: int = 10_000) -> None:
    """클릭을 가로챌 수 있는 두 레이어를 각각 그에 맞는 방법으로 해소한다 —
    다이얼로그 backdrop은 Escape, 로딩 오버레이는 대기. 우리 자신의
    다이얼로그가 열려있는 동안에는 호출하면 안 된다(모듈 docstring의
    "backdrop 처리 시 주의" 참고)."""
    _dismiss_backdrop_if_present(page)
    _wait_for_loading_overlay(page, timeout_ms)


def _is_disabled(button) -> bool:
    """페이지네이션 "다음" 버튼이 실제로 비활성(=마지막 페이지)인지 판정한다.

    실측(2026-08-13)한 버튼 DOM은
    `<button data-disabled="false" ... aria-disabled="false" type="button">`
    형태라, 비활성 여부가 `disabled` 속성이 아니라 `data-disabled`/
    `aria-disabled`로 표현된다. 세 가지를 모두 본다(어느 하나라도 참이면
    비활성) — 배민이 표현 방식을 바꿔도 최소 한 가지는 잡히도록.

    이 판정이 이번 fix의 핵심이다: 클릭 타임아웃 하나만 보고 "마지막
    페이지"로 단정하던 기존 코드는, 오버레이가 클릭을 가로챈 일시적 방해와
    진짜 종료를 구분하지 못해 잘린 결과를 조용히 정상 종료로 취급했다."""
    try:
        if button.evaluate("el => el.disabled") is True:
            return True
    except Exception:
        pass
    for attr in ("data-disabled", "aria-disabled"):
        try:
            if (button.get_attribute(attr) or "").lower() == "true":
                return True
        except Exception:
            continue
    try:
        return not button.is_enabled(timeout=1_000)
    except Exception:
        return False


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


def map_orders_to_daily_sales(order_contents: list[dict]) -> dict[str, int]:
    """`GET /v4/orders` 응답의 `contents[].order.{orderDateTime,payAmount}`를
    날짜별로 합산한다. `orderDateTime`은 `"2026-08-10T22:19:10"` 형태라
    앞 10글자(`YYYY-MM-DD`)만 날짜 키로 쓴다. `fetch_orders`가
    반환한(여러 페이지에 걸쳐 이미 dedup된) flat 리스트를 그대로 받는다 —
    가게통계 화면의 월별 조회가 진행 중인 이번 달을 지원하지 않는 제약(모듈
    docstring의 discrepancy 절 참고)을 주문내역 화면 데이터로 보완하기 위한
    함수다."""
    totals: dict[str, int] = {}
    for item in order_contents:
        order = item["order"]
        date_str = order["orderDateTime"][:10]
        totals[date_str] = totals.get(date_str, 0) + order["payAmount"]
    return totals


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
    조회해도 중복 저장되지 않는다.

    `latest_ordered_at`이 타임존을 가진 값이면(`orders.ordered_at`은
    `TIMESTAMPTZ`라 DB에서 읽어오면 UTC 기준 aware datetime이다) 한국
    시간으로 변환한 뒤 날짜를 뽑는다 — 배민 화면의 날짜 필터는 한국 벽시계
    기준인데, 새벽 주문(KST 00~09시)은 UTC로는 전날이라 변환 없이 `.date()`를
    쓰면 커서가 하루 어긋난다(치킨 배달 특성상 심야 주문이 많아 실제로 자주
    발생하는 경우다)."""
    if latest_ordered_at is not None and latest_ordered_at.tzinfo is not None:
        latest_ordered_at = latest_ordered_at.astimezone(KST)
    if latest_ordered_at is None:
        # 3개월 전 같은 날짜를 계산한다. 월 차감 시 연도 롤오버 처리.
        y, m = today.year, today.month
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
        # 대상 월에 해당 일자가 없으면(예: 2026-05-31 → 2월) 마지막 날로 맞춘다.
        _, last_day = calendar.monthrange(y, m)
        day = min(today.day, last_day)
        return date(y, m, day), today
    return latest_ordered_at.date() - timedelta(days=2), today


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


def map_deposits_by_date(responses: list[dict]) -> dict[str, int]:
    """`GET /v3/settle/history/summary` 응답들의 `contents[].{giveId,
    depositDueDate, giveAmount}`를 날짜별로 합산한다. `giveStatus`(예정/확정)는
    구분하지 않는다(설계 결정) — 상태와 무관하게 금액을 그대로 더한다.
    페이지네이션이 있으면 여러 페이지 응답을 전부 이 리스트에 담아 넘긴다 —
    페이지 경계에 걸친 배치가 두 페이지에 중복으로 나타날 수 있어(실측
    확인: 페이지네이션 중 목록이 갱신되며 겹침 발생) `giveId` 기준으로
    먼저 dedupe한 뒤에 날짜별로 합산한다(리뷰의 `external_review_id`,
    주문내역의 `orderNumber` dedup과 동일한 방어 패턴)."""
    batches_by_id: dict[object, dict] = {}
    for resp in responses:
        for batch in resp["contents"]:
            batches_by_id[batch["giveId"]] = batch

    totals: dict[str, int] = {}
    for batch in batches_by_id.values():
        date_str = batch["depositDueDate"]
        totals[date_str] = totals.get(date_str, 0) + batch["giveAmount"]
    return totals


def _settlement_breakdown_amounts(detail: dict) -> dict[str, int]:
    """정산 상세 응답 하나(`giveId` 하나)에서 4개 카테고리를 양수로 계산한다
    (설계 문서 "API 응답 매핑" 절 공식). `baemin1Details`(한집배달·알뜰배달)와
    `baeminDetails`(가게배달·바로결제) 두 블록을 합산하고, 우가클비용만
    최상위 `cpcDetails`에서 가져온다. 두 블록 중 하나가 없을 수도 있어
    (실측하지 않은 케이스지만 방어적으로) `.get()`으로 안전하게 접근한다.

    모든 중첩 키(`cpcDetails` 포함)에 `.get()`으로 접근한다(2026-08-13
    fix, 최종 리뷰 발견) — 광고비 0원인 매장은 `cpcDetails` 블록 자체가
    응답에 없을 수 있는데(미실측), 여기서 KeyError가 나면
    `map_settlement_breakdown_by_date`가 전체 `details` 리스트를 순회하다
    멈추고, `_run_sync`를 감싸는 상위 `except Exception`이 이번 동기화의
    "정상적으로 캡처된 나머지 배치까지" 전부 버린다 — 배치 하나의 결측이
    전체 동기화 결과를 통째로 무효화하는 위험이라 방어적으로 접근한다."""
    commission = 0
    delivery = 0
    discount = 0
    for block_key in ("baemin1Details", "baeminDetails"):
        block = detail.get(block_key)
        if not block:
            continue
        commission += -block.get("orderBrokerage", {}).get("serviceFeeAmount", {}).get("total", 0)
        commission += -block.get("extra", {}).get("paymentFee", {}).get("total", 0)
        delivery += -block.get("delivery", {}).get("deliverySupplyPrice", {}).get("total", 0)
        discount += -block.get("orderBrokerage", {}).get("benefitsAmount", {}).get("total", 0)
    ad_cost = -(detail.get("cpcDetails") or {}).get("total", 0)
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


def _pick_bounce_month(
    all_recent_months: list[str], requested_months: list[str], *, exclude: set[str] | None = None,
) -> str | None:
    """`requested_months`(이번에 실제로 조회할 달)에도, `exclude`에도 없는
    달을 `all_recent_months`에서 순서대로 찾아 하나 반환한다 — 없으면
    `None`.

    배민 화면은 "이 계정이 마지막으로 조회한 달"을 persist해뒀다가 다음
    진입 시 기본값으로 보여준다. 그 기억된 달과 이번에 요청할 달이 우연히
    같으면, "이미 선택된 달을 다시 선택"하는 조작이 되어 배민 프론트가
    실제 값 변경이 없다고 판단해 API를 재호출하지 않는 현상이 실측
    확인됐다(2026-08-20, 데모 계정 4개 브랜드 전부 — 가게통계/우가클
    양쪽에서 재현). 증분 조회가 매번 같은 1~2개 달만 반복 요청하게
    되면서 처음 드러났다 — 최초 백필은 매번 여러 달을 순회해 항상 직전과
    다른 달을 선택했으므로 우연히 이 문제를 피해갔을 뿐이다. 이 함수가
    고르는 "튕기기" 대상으로 먼저 한 번 선택을 갈아치우면, 그 뒤 실제
    `requested_months` 선택은 항상 진짜 변경이 되어 API 재호출을 보장한다.
    `exclude`는 가게통계처럼 애초에 선택 불가능한 달(진행 중인 이번 달)을
    튕기기 후보에서도 빼야 하는 경우에 쓴다 — 우가클처럼 이번 달도 선택
    가능한 화면은 빈 집합을 넘긴다."""
    exclude = exclude or set()
    for m in all_recent_months:
        if m not in requested_months and m not in exclude:
            return m
    return None


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


def _should_count_sales_response(path: str, collecting: bool) -> bool:
    """`/v3/statistics/orders/summary` 응답 하나가 "매출 엔드포인트를
    관측했다"는 신호(`observed_sales_endpoint`)와 실제 데이터 수집 대상으로
    인정될 수 있는지 판정하는 순수 함수(Playwright 없이 테스트 가능).

    `collecting=False`면(아직 `months` 루프를 시작하기 전, 첫 로드 시점에
    뜨는 예측 불가능한 달의 응답 — 모듈 docstring의 discrepancy 절 참고)
    무조건 False다. 이 게이트가 없으면 (코드 리뷰 지적, 2026-08-11) `months`에
    담긴 모든 달이 배민의 실제 선택 가능 목록과 안 맞아
    `_select_month_dropdown`이 매번 False를 반환해 전부 건너뛰어지는
    상황에서도, discard됐어야 할 그 첫 응답 하나만으로
    `observed_sales_endpoint`가 참이 되어 `fetch_shop_stats`가 에러 없이
    `([], crm_responses)`를 조용히 반환해버리는 은폐된 전체 실패 경로가
    생긴다 — Task 3은 이걸 "이 매장은 매출 이력이 진짜 0건"이라는 정상
    케이스와 구분할 방법이 없었다. `observed_sales_endpoint`와 실제 데이터
    수집 둘 다 같은 게이트(`months` 루프가 실제로 시작된 뒤라는 조건)를
    써야 이 실패 모드가 막힌다."""
    return path == "/v3/statistics/orders/summary" and collecting


def fetch_shop_stats(page, shop_no: int, months: list[str]) -> tuple[list[dict], list[dict]]:
    """가게통계 화면(`/shops/{shop_no}/stat`)에서 매출(statistics/orders/summary)과
    재주문율(crmInfo) organic 응답을 가로챈다. `months`에 담긴 각 달마다 월
    선택 드롭다운을 조작해 그 달 데이터를 로드시킨다 — crmInfo는 월과 무관한
    고정 최근 7일 창이라 첫 로드에서만 캡처하고 이후 월 이동에서는 무시한다.

    `months`에 진행 중인 이번 달이 포함돼 있으면 그 달은 조용히 건너뛴다
    (모듈 docstring의 discrepancy 절 참고) — 반환되는 `sales_responses`가
    `len(months)`보다 적을 수 있다.

    `crm_responses`는 빈 리스트일 수 있다 — "신규·재주문" 위젯은 화면
    하단에서 지연 로드되는데, 정확한 헤딩 텍스트(가운뎃점 구분자, 하이픈
    아님) + 뷰포트 중앙 스크롤 + 스크롤 재시도까지 다 갖춘 뒤에도 이 함수
    호출 4번 중 2번은 여전히 안 잡혔다(원인 규명·수정 경위는 모듈 docstring
    "crmInfo 재조사 결과" 절 참고) — 매출/정산/주문내역과 달리 이 잔여
    flakiness 때문에 crmInfo 미관측은 하드 에러로 취급하지 않는다.
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
        # 중복으로 합산될 위험이 있어, 명시적으로 월을 선택하기 시작한 뒤부터만
        # 수집한다 — `observed_sales_endpoint`도 이 게이트를 함께 쓴다
        # (`_should_count_sales_response` 참고, 코드 리뷰로 발견된 버그 수정:
        # 예전에는 관측 플래그만 게이트 없이 서서 discard된 첫 응답 하나로도
        # 참이 될 수 있었다).
        "collect_sales": False,
    }

    def _on_response(response) -> None:
        url = response.url
        path = urlparse(url).path
        if "self-api.baemin.com" not in url:
            return
        if _should_count_sales_response(path, state["collect_sales"]):
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

        page.wait_for_timeout(3_000)  # 첫 로드(예측 불가한 기본 달) 대기
        _dismiss_backdrop_if_present(page)
        # "신규·재주문"(crmInfo) 위젯은 화면 하단에 스켈레톤 placeholder로
        # 렌더링된 채 대기하다가 실제로 뷰포트에 들어와야 organic 요청이
        # 발생한다(실측 확인). 처음에는 "신규-재주문"(하이픈)으로 헤딩을
        # 찾았는데 실제 DOM 텍스트는 "신규·재주문"(가운뎃점, U+00B7 —
        # "일・주" 탭처럼 이 사이트가 구분자로 흔히 쓰는 문자)이라 전혀
        # 매칭되지 않았고, 매칭 실패 시 쓰던 fallback(고정 1200px 스크롤)이
        # 위젯을 정확히 뷰포트 안에 넣지 못해 지연 로드가 트리거되지 않았던
        # 것으로 재현·확인했다. 구분자 문자에 의존하지 않도록 "재주문"
        # 부분 문자열로 찾고, 뷰포트 가장자리가 아니라 세로 중앙에 오도록
        # 정확히 스크롤한다(일부 지연 로드 위젯은 IntersectionObserver
        # threshold가 엄격해 가장자리 노출만으로는 안 뜬다) — 이렇게 하니
        # 재현 시 스크롤 후 2초 만에 응답이 잡혔다.
        # 헤딩 자체가 아직 DOM에 안 붙었을 수도 있다(첫 로드 직후 3초 대기가
        # 매번 충분하다는 보장이 없다 — 실측 재현에서 세션마다 편차가 있었다)
        # — 짧게 재시도하며 나타나길 기다린다.
        heading = page.get_by_text(re.compile(r"재주문")).first
        for _ in range(5):
            if heading.count() > 0:
                break
            page.wait_for_timeout(1_000)

        if heading.count() > 0:
            box = heading.bounding_box()
            if box:
                viewport = page.viewport_size
                target_scroll_y = box["y"] - (viewport["height"] / 2) + (box["height"] / 2)
                page.evaluate(f"window.scrollBy(0, {target_scroll_y})")
        else:
            # 헤딩을 끝내 못 찾았으면(텍스트가 또 바뀌었거나 렌더가 유난히
            # 느린 경우) 최소한 대략적인 스크롤이라도 시도해 위젯이 뷰포트에
            # 걸릴 가능성을 높인다 — 정밀 타겟팅의 대체제일 뿐이라 이 경로만
            # 믿지는 않는다(아래 폴링에서도 여러 단계로 나눠 스크롤한다).
            for _ in range(3):
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(800)
                if state["observed_crm_endpoint"]:
                    break

        # 한 번의 긴 대기 대신 짧게 나눠 최대 12초까지 기다리며 잡히는 대로
        # 바로 빠져나온다(느린 세션에서도 불필요하게 오래 기다리지 않기 위함).
        for _ in range(6):
            page.wait_for_timeout(2_000)
            if state["observed_crm_endpoint"]:
                break

        if not state["observed_crm_endpoint"] and heading.count() > 0:
            # 그래도 안 잡히면 위로 스크롤했다가 다시 중앙으로 되돌아온다 —
            # 페이지가 이미 그 위치로 스크롤된 채 로드됐다면(예: 브라우저의
            # 스크롤 위치 복원) IntersectionObserver가 "새로 진입"하는
            # 이벤트 자체가 안 생겨서 최초 진입 트리거를 놓쳤을 수 있다.
            # 스크롤을 위로 뺐다가 다시 중앙으로 넣으면 새 진입 이벤트를
            # 강제로 만들 수 있다.
            page.evaluate("window.scrollBy(0, -400)")
            page.wait_for_timeout(500)
            box = heading.bounding_box()
            if box:
                viewport = page.viewport_size
                target_scroll_y = box["y"] - (viewport["height"] / 2) + (box["height"] / 2)
                page.evaluate(f"window.scrollBy(0, {target_scroll_y})")
            for _ in range(3):
                page.wait_for_timeout(2_000)
                if state["observed_crm_endpoint"]:
                    break

        # "마지막 조회한 달 기억" 문제 방어(_pick_bounce_month docstring
        # 참고) — 가게통계는 진행 중인 이번 달을 애초에 선택할 수 없으므로
        # 튕기기 후보에서도 뺀다. 이 튕기기 자체의 응답은 아래
        # `state["collect_sales"]`가 아직 False인 시점에 발생하므로
        # `_should_count_sales_response`가 이미 알아서 무시한다(첫 로드의
        # 예측 불가능한 응답을 무시하는 것과 동일한 게이트) — 별도 처리가
        # 필요 없다. 튕기기 자체가 실패해도 본 조회는 그대로 시도한다.
        bounce_month = _pick_bounce_month(recent_months(3), months, exclude={recent_months(1)[0]})
        if bounce_month is not None:
            try:
                _select_month_dropdown(page, bounce_month)
                page.wait_for_timeout(1_000)
            except PlaywrightTimeoutError:
                pass

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
        diagnostics = capture_failure_diagnostics(page, f"shop-stats-{shop_no}")
        raise BaeminStatsScrapeError(f"매출 통계 API 응답을 한 번도 확인하지 못했습니다 ({diagnostics})")
    # crmInfo는 매출/정산/주문내역과 달리 하드 실패시키지 않는다(fix round
    # 재조사 결론, 모듈 docstring의 "crmInfo 재조사 결과" 절 참고) — 근본
    # 원인(헤딩 텍스트 구분자 불일치로 스크롤 타겟팅 자체가 실패) 하나는
    # 실제로 찾아 고쳤고 그 수정이 격리된 진단 스크립트에서 두 차례
    # 재현됐지만, 같은 수정을 `fetch_shop_stats` 안에서 실행했을 때는
    # 여전히 못 잡을 때가 있었다(4번 중 2번 성공) — 위젯 자체의 지연 로드
    # 타이밍이 클라이언트 쪽 스크롤 정밀도만으로는 완전히 통제되지 않는
    # 잔여 flakiness로 보인다. 매출/정산/주문내역은 이 fix round 동안 매번
    # 안정적으로 잡혔던 것과 대비된다.
    return sales_responses, crm_responses


def _open_date_range_picker(page) -> None:
    """정산내역/주문내역 화면의 "날짜 직접 선택" 버튼을 눌러 "기간"
    다이얼로그를 연다. 실측 확인: 이 다이얼로그는 "날짜"(직접 선택) 탭이 이미
    기본 활성 상태로 열린다 — 별도로 탭을 클릭할 필요가 없다(모듈 docstring
    참고).

    2026-08-13 fix: 이 클릭이 오버레이에 가로채여 타임아웃 나는 것을 실
    계정에서 재현했다(로그: `<div class="LoadingBackdrop-module__...">
    intercepts pointer events`, 이어서 `data-testid="backdrop"`도 같은
    클릭을 가로챔). 두 레이어는 해소 방법이 서로 다르므로
    `_clear_click_blockers`로 각각 처리한 뒤 재시도한다 — 한 번의 타임아웃을
    곧장 실패로 확정하면, 실제로는 잠깐 뜬 로딩 오버레이 때문에 그 동기화
    소스 전체가 통째로 실패한다."""
    last_error: Exception | None = None
    for _attempt in range(3):
        _clear_click_blockers(page)
        try:
            page.get_by_text("날짜 직접 선택").first.click(timeout=5_000)
            page.wait_for_timeout(800)
            return
        except PlaywrightTimeoutError as e:
            last_error = e
            page.wait_for_timeout(1_500)
    raise last_error if last_error is not None else BaeminStatsScrapeError("날짜 선택 버튼을 열지 못했습니다")


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
        if not captions:
            # 캘린더가 안 열린 상태(범위 표시 클릭이 먹지 않았을 때)를 원시
            # ValueError(`min()` on empty) 대신 알아볼 수 있는 에러로 바꾼다 —
            # 호출자(`_set_date_range`)가 이걸 잡아 재시도한다.
            raise BaeminStatsScrapeError("날짜 캘린더가 열리지 않았습니다(월 표시를 찾지 못함)")
        min_key = min(y * 12 + m for y, m in captions)
        nav_label = "이전 달" if target_key < min_key else "다음 달"
        dialog.get_by_role("button", name=nav_label).click(timeout=3_000)
        dialog.page.wait_for_timeout(300)
    raise BaeminStatsScrapeError(f"{year}-{month:02d}-{day:02d} 날짜를 캘린더에서 찾지 못했습니다")


def parse_applied_range(text: str) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """"기간" 다이얼로그의 날짜 범위 표시 문구를 (시작, 종료) 튜플로 파싱한다.
    파싱할 수 없으면 `None`(호출자는 "요청한 범위가 아님"으로 취급한다 —
    모호하면 조용히 통과시키지 않고 재시도/에러로 간다).

    실측 확인된 형태(2026-08-13, 주문내역 화면):
    - 범위: `"2026. 8. 7 ~ 8. 13"` — 숫자 5개(연도는 왼쪽에만 나온다)
    - 단일: `"2026. 8. 13"` — 숫자 3개(시작=종료인 하루짜리 선택)
    해가 바뀌는 범위(`"2025. 12. 30 ~ 2026. 1. 5"`, 숫자 6개)는 실측하지
    못했지만 형태상 가능성이 있어 함께 받아준다 — 1~3월에 3개월 백필을
    하면 실제로 발생하는 경우다."""
    nums = [int(n) for n in _APPLIED_RANGE_RE.findall(text)]
    if "~" not in text:
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2]), (nums[0], nums[1], nums[2])
        return None
    if len(nums) == 5:
        return (nums[0], nums[1], nums[2]), (nums[0], nums[3], nums[4])
    if len(nums) == 6:
        return (nums[0], nums[1], nums[2]), (nums[3], nums[4], nums[5])
    return None


def _range_display_locator(dialog):
    """"기간" 다이얼로그에서 클릭하면 캘린더가 열리는 날짜 범위 표시 요소.

    기본은 원래부터 쓰던 "~" 포함 텍스트다 — 세 화면(주문내역/정산내역/정산
    상세) 전부에서 실측 검증된 선택자라 그대로 유지한다. 날짜 선택이 한 번
    빗나가면 표시가 하루짜리 단일 날짜("2026. 8. 13")로 바뀌어 "~"가 아예
    없어지는데, 그 상태에서는 재시도조차 못 하므로 **그때만** 더 넓은 패턴
    으로 폴백한다.

    처음부터 넓은 패턴 하나로 찾으면 안 된다(2026-08-13, 실 계정 A/B로 확인한
    회귀): 후보가 늘어나면서 `.first`가 실제 범위 표시가 아닌 다른 요소로
    리졸브돼 클릭해도 캘린더가 열리지 않았고, 정산 상세 수집이 20건 → 크래시로
    깨졌다(입금 90일 창은 같은 코드인데도 멀쩡했다 — 화면 상태에 따라 갈리는
    종류의 회귀라 라이브 검증 없이는 못 잡았을 것이다)."""
    tilde = dialog.get_by_text(re.compile(r"~"))
    if tilde.count() > 0:
        return tilde.first
    return dialog.get_by_text(re.compile(r"\d{4}\. \d")).first


def _set_date_range(page, start_date: str, end_date: str) -> None:
    """열린 "기간" 다이얼로그에서 `start_date`~`end_date`("YYYY-MM-DD")를
    지정하고 적용한다. 날짜 범위 표시 텍스트를 클릭해 두 달짜리 캘린더를 연
    뒤, 시작일 → 종료일 순으로 클릭하고, 캘린더 자체의 "적용"과 상위 "기간"
    다이얼로그의 "적용"을 순서대로 누른다 — 두 다이얼로그가 겹쳐있는 동안
    "적용" 버튼도 두 개 동시에 존재하므로 각 클릭을 정확히 그 시점의 최상단
    다이얼로그로 scope한다(모듈 docstring 참고).

    **정정 (2026-08-13, 최종 리뷰 fix round — 실 계정으로 재현·수정): 두 번
    클릭하면 범위가 선택된다는 가정이 틀렸다.** 이 캘린더는 상태 기계라,
    "시작일 클릭 → 종료일 클릭"이 **진입 시점의 기존 선택 상태에 따라**
    전혀 다른 결과를 낸다(실측 재현):

    | 진입 상태 | 클릭 | 결과 |
    |---|---|---|
    | 범위 `[7,13]` 선택됨 | `5` (범위 밖) | 새 선택 시작 → 대기 `[5]` |
    | 대기 `[5]` | `10` (뒤 날짜) | 범위 완성 `[5,10]` ✅ |
    | 범위 `[7,13]` 선택됨 | `8` (범위 **안쪽**) | 시작은 그대로, 끝만 이동 → `[7,8]` ❌ |
    | 범위 `[7,8]` | `12` (범위 밖) | 새 선택 시작 → 대기 `[12]` ❌ |
    | 대기 `[12]` | `6` (앞 날짜) | 새 선택 시작 → 대기 `[6]` ❌ |
    | 범위 `[8,13]` | `13` (끝점) | 새 선택 시작 → 대기 `[13]` ❌ |

    즉 요청한 시작일이 "이미 선택돼 있던 범위 안쪽"에 들어가면, 두 번의
    클릭이 끝나도 우리가 요청한 범위가 아니라 **엉뚱한 범위(또는 하루짜리
    단일 날짜)** 가 선택된다. 기존 구현은 이걸 전혀 확인하지 않고 그대로
    "적용"을 눌렀기 때문에, 화면은 우리가 요청한 적 없는 범위(대개 하루,
    혹은 화면 기본값인 최근 7일)로 조회되는데 호출자는 그 사실을 알 방법이
    없었다 — 3개월 백필을 요청하고도 조용히 하루치만 받는 실패가 실제로
    재현됐다(요청 `2026-08-08~2026-08-13` → 실제 적용 `2026-08-13~2026-08-13`).

    그래서 이제는 **적용 결과를 읽어서 요청한 범위와 대조하고, 다르면 다시
    시도한다**. 재시도가 수렴하는 이유는 위 상태 기계 때문이다 — 실패한
    시도가 남긴 상태(하루짜리 대기 선택 또는 짧은 범위)에서 같은 클릭 쌍을
    다시 하면 이번엔 "범위 밖 클릭 → 새 선택 시작 → 뒤 날짜 클릭 → 범위
    완성"으로 떨어진다(실측: 2회차에 항상 성공). 끝내 못 맞추면 조용히
    잘못된 범위를 쓰는 대신 `BaeminStatsScrapeError`를 던진다."""
    start_y, start_m, start_d = (int(p) for p in start_date.split("-"))
    end_y, end_m, end_d = (int(p) for p in end_date.split("-"))
    expected = ((start_y, start_m, start_d), (end_y, end_m, end_d))
    applied_text = ""

    for attempt in range(_MAX_DATE_RANGE_ATTEMPTS):
        try:
            period_dialog = page.get_by_role("dialog").last
            _range_display_locator(period_dialog).click(timeout=5_000)
            page.wait_for_timeout(800)

            cal_dialog = page.get_by_role("dialog").last
            _click_calendar_day(cal_dialog, start_y, start_m, start_d)
            page.wait_for_timeout(400)
            cal_dialog = page.get_by_role("dialog").last
            _click_calendar_day(cal_dialog, end_y, end_m, end_d)
            page.wait_for_timeout(400)

            cal_dialog = page.get_by_role("dialog").last
            cal_dialog.get_by_role("button", name="적용").first.click(timeout=5_000)
            page.wait_for_timeout(800)

            period_dialog = page.get_by_role("dialog").last
            applied_text = _range_display_locator(period_dialog).inner_text(timeout=3_000)
        except (PlaywrightTimeoutError, BaeminStatsScrapeError) as e:
            # 캘린더가 안 열렸거나 클릭이 가로채인 경우 — 마지막 시도가
            # 아니면 상태를 정리하고 다시 해본다(한 번의 일시적 방해로
            # 이 소스 전체를 실패시키지 않는다).
            if attempt == _MAX_DATE_RANGE_ATTEMPTS - 1:
                raise BaeminStatsScrapeError(
                    f"날짜 범위를 {start_date}~{end_date}로 지정하지 못했습니다: {e}"
                ) from e
            applied_text = ""
            page.wait_for_timeout(1_000)
            continue
        if parse_applied_range(applied_text) == expected:
            break
    else:
        raise BaeminStatsScrapeError(
            f"날짜 범위를 {start_date}~{end_date}로 지정하지 못했습니다"
            f"(화면에 적용된 범위: {applied_text or '확인 불가'})"
        )

    remaining = page.get_by_role("dialog").all()
    if remaining:
        outer_apply = remaining[-1].get_by_role("button", name="적용")
        if outer_apply.count() > 0:
            outer_apply.first.click(timeout=5_000)


def _click_load_more_until_done(page, progress_fn) -> None:
    """"더보기" 버튼을 연속 무진행이 감지될 때까지 반복 클릭한다.
    `baemin_reviews.py`의 리뷰 리스트 페이지네이션과 동일한 패턴이고, 정산
    내역 화면에서도 실측으로 동일하게 확인됐다(리스트 하단에 "더보기" 텍스트
    버튼, 스크롤만으로는 추가 페이지가 로드되지 않음). 주문내역 화면은 이
    패턴을 쓰지 않는다 — `_click_next_page_until_done` 참고.
    `progress_fn()`은 현재까지 수집한 항목 수를 반환해야
    한다(클릭 전후로 비교해 진행 여부를 판단).

    "더보기" 버튼은 리스트가 끝에 도달해도 사라지지 않을 수 있다(리뷰
    리스트에서 실측 확인된 동작) — 그래서 버튼 존재 여부(`count()==0`)는
    보너스 조기 종료일 뿐, 연속 무진행 카운터가 실질적인 종료 조건이다.
    """
    consecutive_no_progress = 0
    for _ in range(_MAX_LOAD_MORE_CLICKS):
        more_button = page.get_by_text("더보기", exact=True)
        if more_button.count() == 0:
            break
        before = progress_fn()
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
        if progress_fn() > before:
            consecutive_no_progress = 0
        else:
            consecutive_no_progress += 1
            if consecutive_no_progress >= _MAX_CONSECUTIVE_NO_PROGRESS:
                break


def _click_next_page_until_done(page, progress_fn, max_clicks: int = _MAX_LOAD_MORE_CLICKS) -> str:
    """주문내역/정산내역 화면의 숫자 페이지네이션(`1 2 3 ... 20` + 접근성
    이름 `"다음"`인 다음-페이지 버튼)을 끝까지 반복 클릭한다. 클릭할 때마다
    `offset`이 10씩 늘어난 요청이 새로 나간다(실측 확인).
    `_click_load_more_until_done`("더보기" 텍스트 버튼)과는 다른 UI라 별도
    헬퍼로 분리했다(모듈 docstring의 2026-08-12 정정 절 참고).
    `progress_fn()`은 현재까지 수집한 항목 수를 반환해야 한다.

    **종료 사유를 문자열로 반환한다**(2026-08-13 fix): `"no_button"`(버튼
    자체가 없음) / `"disabled"`(버튼이 실제로 비활성 — 진짜 마지막 페이지) /
    `"no_progress"`(버튼은 살아있는데 새 데이터가 안 옴) / `"blocked"`(오버레이가
    클릭을 계속 가로챔) / `"max_clicks"`(상한 도달). 호출자는 이 값으로
    "정상 종료"와 "중단"을 구분할 수 있다 — 다만 이 반환값만으로 완결성을
    판단하지는 말아야 한다(`fetch_orders`는 응답의 `totalSize`와 실제 수집
    건수를 대조하는 별도 게이트를 함께 쓴다).

    **왜 고쳤나 (2026-08-13, 최종 리뷰 fix round)**: 원래 구현은 (1) 상한이
    30회로 고정이라 3개월 백필(실측 1,541건 = 155페이지)이 구조적으로
    불가능했고, (2) `except PlaywrightTimeoutError: break`가 "버튼이 비활성
    이라 클릭이 타임아웃난 진짜 종료"와 "backdrop/로딩 오버레이가 클릭을
    가로챈 일시적 방해"를 완전히 같은 신호로 취급했으며, (3) 클릭 후 고정
    1.5초만 기다리고 진행이 없으면 곧장 종료해서, 응답이 조금만 늦어도
    (실측 재현됨) 남은 페이지를 통째로 버렸다. 셋 다 결과가 "일부만 수집한
    뒤 조용히 성공한 척"이라, 주문내역처럼 최신순으로 잘리는 목록에서는
    가장 오래된 구간이 영구히 유실되는 원인이 됐다.

    이제는 (1) 상한을 호출자가 정하고(기본값은 기존 30 유지 — 정산내역 등
    기존 호출자의 동작을 그대로 보존), (2) 클릭 실패 시 버튼의 실제 비활성
    여부(`_is_disabled`)를 확인해 진짜 종료가 아니면 오버레이를 해소하고
    같은 페이지를 재시도하며, (3) 진행 여부를 고정 대기가 아니라 폴링으로
    기다리고, 그래도 진행이 없으면 **같은 페이지를 몇 번 더 눌러본다**.

    (3)의 재시도가 왜 필요한가: 3개월 백필은 155페이지짜리라, 페이지 하나가
    일시적으로 느리거나 응답을 흘리면 그것만으로 백필 전체가 실패한다(실측:
    67페이지째에서 한 번 멈춰 670/1541에서 중단됐다). 한 번의 무진행을 곧장
    종료로 확정하지 않고 다시 눌러보면 대부분 그대로 이어진다 — "다음"을
    한 번 더 누르는 건 데이터 유실 위험이 없다(어차피 모든 응답을 리스너가
    수집하고, 마지막엔 `totalSize` 대조가 완결성을 최종 판정한다)."""
    for _ in range(max_clicks):
        next_button = page.get_by_role("button", name="다음")
        if next_button.count() == 0:
            return "no_button"
        button = next_button.first
        if _is_disabled(button):
            return "disabled"

        before = progress_fn()
        progressed = False
        clicked_any = False
        for _attempt in range(_NEXT_PAGE_BLOCKED_RETRIES):
            # 클릭 직전에 오버레이를 해소한다 — 로딩 오버레이는 Escape로
            # 닫히지 않으므로 사라질 때까지 기다리는 게 유일한 대응이다.
            if page.get_by_test_id("backdrop").count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            _wait_for_loading_overlay(page)
            try:
                button.scroll_into_view_if_needed()
                button.click(timeout=5_000)
                clicked_any = True
            except PlaywrightTimeoutError:
                # 여기서 갈린다: 버튼이 실제로 비활성이면 정상 종료이고,
                # 그게 아니면 무언가가 클릭을 가로챈 것이라 재시도해야 한다.
                if _is_disabled(button):
                    return "disabled"
                page.wait_for_timeout(_LOAD_MORE_WAIT_MS)
                continue

            # 고정 대기 대신, 실제로 새 데이터가 도착할 때까지 폴링한다.
            waited = 0
            while waited < _NEXT_PAGE_PROGRESS_TIMEOUT_MS and progress_fn() <= before:
                page.wait_for_timeout(_NEXT_PAGE_POLL_MS)
                waited += _NEXT_PAGE_POLL_MS
            if progress_fn() > before:
                progressed = True
                break
            # 진행이 없다 — 마지막 페이지라 버튼이 방금 비활성이 됐을 수도
            # 있고(정상 종료), 일시적으로 응답을 놓친 것일 수도 있다(재시도).
            if _is_disabled(page.get_by_role("button", name="다음").first):
                return "disabled"

        if not progressed:
            return "no_progress" if clicked_any else "blocked"
    return "max_clicks"


def fetch_account_settlement(page, start_date: str, end_date: str) -> list[dict]:
    """정산내역 화면(`/orders/billing`)에서 계정 전체 입금 배치
    (settle/history/summary) organic 응답을 가로챈다.

    **정정 (2026-08-11 세션 중 재현·수정) — "더보기"가 아니라 숫자
    페이지네이션이다.** 원래 서술("더보기" 버튼, 리뷰 리스트와 동일한
    패턴)은 틀렸다 — 실 계정으로 재현해보니 정산내역 리스트 자체에는
    "더보기" 텍스트가 없고, `page.get_by_text("더보기", exact=True)`가
    실제로 클릭하고 있던 건 화면 하단의 무관한 "전문가 Q&A" 섹션의
    "더보기 ›" 링크였다 — 그래서 아무리 클릭해도 정산 목록에 새 요청이
    전혀 발생하지 않고 항상 첫 페이지(10건)만 수집됐다(`totalSize=20`인데
    10건만 잡히는 것으로 재현 확인). 주문내역(`_click_next_page_until_done`
    도입 절 참고)과 마찬가지로 숫자 페이지네이션(`1 2 3 ...` + 접근성 이름
    `"다음"`인 다음-페이지 버튼)이라 그 헬퍼를 그대로 재사용한다.

    날짜 범위를 지정하기 **전**(페이지 최초 로드 시점)에도 화면이 스스로
    기본 필터(짧은 미정산 예정 구간)에 대한 organic 응답을 한 번
    발생시킨다(실측 확인 — `startDate`가 우리가 지정한 범위가 아닌 것으로
    구분됨). 이 응답이 결과에 섞이면 우리가 요청한 범위 밖 날짜가
    끼어들거나, 겹치는 날짜의 배치가 이중 집계될 위험이 있다 — 그래서
    `fetch_shop_stats`/`fetch_brand_click_metrics`와 동일한 `collecting`
    플래그 게이트로 걸러낸다.

    **완결성 보장 (2026-08-19, 증분 조회 전환 최종 리뷰 fix round)**: 이
    함수도 이제 "요청한 범위의 정산 배치를 하나도 빠짐없이 가져왔다"가
    확인될 때만 정상 반환한다. 원래는 `observed_any`(응답을 한 번이라도
    봤는가) 하나만 확인해서, 페이지네이션이 도중에 끊기면 첫 페이지 10건만
    담긴 리스트를 그대로 성공으로 반환했다 — 이 함수가 겪었던 실제 버그
    (위 "정정" 절: `totalSize=20`인데 10건만 수집)가 정확히 그 형태다.
    입금 조회가 고정 90일 창에서 커서 기반 증분 조회로 바뀐 뒤로는 이
    은폐된 실패의 대가가 훨씬 커졌다: 부분 수집을 저장하면
    `daily_settlements`의 커서가 오늘로 전진해, 수집하지 못한 구간을 이후
    어떤 동기화도 다시 쳐다보지 않는 영구 유실이 된다("부분 수집을 절대
    성공으로 취급하지 않는다" — CLAUDE.md).

    판정 근거는 응답 본문의 `totalSize`(실측 확인된 필드)다. 페이지네이션으로
    모은 **고유 `giveId`** 개수와 대조한다 — 페이지 경계에서 같은 배치가 두
    페이지에 겹쳐 나오는 현상이 실측으로 확인됐기 때문에(`map_deposits_by_date`
    docstring 참고) 항목 수를 그대로 세면 부분 수집을 완전 수집으로
    오판한다. 여러 페이지의 `totalSize`가 서로 다르면(조회 도중 새 배치가
    생기면 늘어난다) `fetch_orders`와 동일하게 가장 작은 값을 기준으로 삼아
    false 에러를 막는다."""
    responses: list[dict] = []
    state = {"observed_any": False, "collecting": False}

    def _should_count(url: str, collecting: bool) -> bool:
        if "self-api.baemin.com" not in url:
            return False
        return urlparse(url).path == "/v3/settle/history/summary" and collecting

    def _on_response(response) -> None:
        if not _should_count(response.url, state["collecting"]):
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
            page.goto("https://self.baemin.com/orders/billing")
        except Exception as e:
            raise BaeminStatsScrapeError(f"정산내역 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(2_000)
        _dismiss_backdrop_if_present(page)

        state["collecting"] = True
        try:
            _open_date_range_picker(page)
            _set_date_range(page, start_date, end_date)
        except PlaywrightTimeoutError as e:
            raise BaeminStatsScrapeError(f"정산내역 날짜 범위 지정에 실패했습니다: {e}") from e
        page.wait_for_timeout(2_000)

        stop_reason = _click_next_page_until_done(page, lambda: len(responses))
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminStatsScrapeError("정산내역 API 응답을 한 번도 확인하지 못했습니다")

    # 부분 페이지네이션 하드 에러 게이트(docstring "완결성 보장" 절 참고).
    # `totalSize`가 아예 없는 응답 형태(미실측)라면 판정을 건너뛴다 — 없는
    # 신호로 false 에러를 만들지는 않는다.
    total_sizes = [r["totalSize"] for r in responses if isinstance(r.get("totalSize"), int)]
    if total_sizes:
        expected_total = min(total_sizes)
        collected_give_ids = {
            batch["giveId"]
            for resp in responses
            for batch in (resp.get("contents") or [])
            if "giveId" in batch
        }
        if len(collected_give_ids) < expected_total:
            raise BaeminStatsScrapeError(
                f"정산내역 {start_date}~{end_date} 총 {expected_total}건 중 "
                f"{len(collected_give_ids)}건만 수집했습니다(페이지네이션 종료 사유: {stop_reason}) "
                f"— 부분 수집을 저장하면 커서가 전진해 미수집 구간이 영구 유실되므로 실패로 처리합니다"
            )

    return responses


def fetch_orders(
    page, start_date: str, end_date: str, max_page_clicks: int = _MAX_ORDER_PAGE_CLICKS,
) -> list[dict]:
    """주문내역 화면(`/orders/history`)에서 `start_date`~`end_date`("YYYY-MM-DD")
    범위의 주문(`/v4/orders`) organic 응답을 가로챈다. 가게통계 화면의 월별 조회가
    진행 중인 이번 달을 지원하지 않는다는 제약(모듈 docstring의 discrepancy
    절 참고)을 보완하기 위한 함수 — 호출자는 이 함수가 반환한 `contents`
    항목 리스트를 `map_orders_to_daily_sales`로 집계해 이번 달 매출만 별도로
    채운다.

    2026-08-13부터는 두 가지 목적으로 함께 쓰인다: (1) 기존처럼 이번 달
    1일~오늘 범위로 호출해 진행 중인 이번 달 매출을 보완하고(호출자가
    `map_orders_to_daily_sales`로 집계), (2) `compute_order_sync_range`가
    계산한 범위로 호출해 개별 주문 자체를 `orders` 테이블에 저장한다
    (호출자가 `map_order_rows`로 매핑). 날짜 범위만 다를 뿐 스크래핑
    로직은 완전히 동일하다 — 원래 함수 내부에 하드코딩돼 있던 "이번 달
    1일~오늘"을 호출자가 넘기는 인자로 바꿨을 뿐이다.

    날짜 범위 지정은 정산내역(`/orders/billing`)과 완전히 동일한 공유
    다이얼로그 컴포넌트를 그대로 재사용한다(실측 확인 — "날짜 직접 선택"
    클릭 시 뜨는 "기간" 다이얼로그와 두 달짜리 캘린더 구조가 정산내역과
    동일했다) — 그래서 `_open_date_range_picker`/`_set_date_range`를 그대로
    호출한다. 페이지네이션은 정산내역과 다르다 — "더보기" 버튼이 없고
    숫자 페이지네이션을 쓴다(모듈 docstring의 2026-08-12 정정 절 참고) —
    그래서 `_click_load_more_until_done`이 아니라 `_click_next_page_until_done`을
    쓴다.

    `shopNumbers` 쿼리 파라미터가 빈 채로 나가는 것을 실측 확인했다 —
    가게통계(브랜드별로 shop_no를 순회해야 함)와 달리 이 화면은 계정에
    연결된 모든 브랜드를 한 번의 조회로 함께 반환하므로, `fetch_shop_stats`와
    달리 `shop_no` 인자를 받지 않고 브랜드별 반복도 하지 않는다.

    반환값은 `contents` 항목을 `order.orderNumber` 기준으로 중복 제거해
    합친 flat 리스트다(리뷰 리스트의 `id` 기준 dedup과 동일한 방어적
    패턴 — 페이지네이션 경계에서 항목이 겹칠 가능성에 대비).

    **완결성 보장 (2026-08-13, 최종 리뷰 fix round)**: 이 함수는 이제 "요청한
    범위의 주문을 하나도 빠짐없이 가져왔다"가 확인될 때만 정상 반환한다.
    근거는 응답 본문의 `totalSize`다 — 실 계정 조사에서 `/v4/orders` 응답
    최상위에 `{"totalSize": int, "totalPayAmount": int, "contents": [...]}`가
    있는 걸 확인했고(예: 3개월 창 1,541건, 최근 7일 126건), 이는
    `fetch_settlement_breakdown_details`가 쓰는 정산 summary의 `totalSize`
    부분 캡처 게이트와 정확히 같은 종류의 신호다. 수집 건수가 `totalSize`에
    못 미치면 조용히 잘린 리스트를 반환하는 대신 `BaeminStatsScrapeError`를
    던진다.

    이게 왜 중요한가: 개별 주문 저장의 증분 커서는 `MAX(ordered_at)`이고
    주문 목록은 최신순이라, 잘린 수집은 항상 **가장 오래된 구간**을 버린다.
    그런데도 성공으로 반환하면 커서는 오늘까지 전진해버려서, 버려진 구간은
    이후 어떤 동기화도 다시 쳐다보지 않는 영구 유실이 된다. 하드 에러로
    표면화해야 `_run_sync`가 그 소스를 실패로 기록하고 커서가 그대로 남아
    다음 동기화가 같은 구간을 다시 시도한다.

    또한 화면 기본 필터(최근 7일)에 대한 organic 응답이 우리 요청과 무관하게
    먼저 발생하므로(실측 확인 — `startDate=2026-08-07&endDate=2026-08-13`),
    요청 URL의 `startDate`/`endDate`가 우리가 지정한 범위와 정확히 일치하는
    응답만 수집한다(`fetch_shop_stats`/`fetch_account_settlement`의
    `collecting` 게이트와 같은 목적이지만, 플래그가 아니라 요청 파라미터
    자체를 대조하는 더 엄격한 방식이다). 이 게이트가 없으면 날짜 범위 지정이
    빗나갔을 때 기본 7일치 데이터를 "요청한 3개월"인 양 반환하는 은폐된
    실패가 생긴다.

    `max_page_clicks`는 페이지네이션 클릭 상한이다. 기본값은 이 함수의 기본
    용도(이번 달 1일~오늘)를 넉넉히 덮는 값이고, 3개월 백필처럼 훨씬 깊은
    조회는 호출자가 `ORDER_BACKFILL_PAGE_CLICKS`를 넘긴다."""
    collected: dict[object, dict] = {}
    state = {"observed_any": False, "matched_responses": 0, "total_sizes": []}

    def _on_response(response) -> None:
        parsed = urlparse(response.url)
        if parsed.path != "/v4/orders":
            return
        state["observed_any"] = True
        if response.status != 200:
            return
        query = parse_qs(parsed.query)
        if (query.get("startDate") or [""])[0] != start_date:
            return
        if (query.get("endDate") or [""])[0] != end_date:
            return
        try:
            body = response.json()
        except Exception:
            return
        state["matched_responses"] += 1
        total_size = body.get("totalSize")
        if isinstance(total_size, int):
            state["total_sizes"].append(total_size)
        for item in body.get("contents", []):
            order_number = item.get("order", {}).get("orderNumber")
            key = order_number if order_number is not None else id(item)
            collected[key] = item

    page.on("response", _on_response)
    try:
        try:
            page.goto("https://self.baemin.com/orders/history")
        except Exception as e:
            raise BaeminStatsScrapeError(f"주문내역 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(2_000)
        _clear_click_blockers(page)
        try:
            _open_date_range_picker(page)
            _set_date_range(page, start_date, end_date)
        except PlaywrightTimeoutError as e:
            raise BaeminStatsScrapeError(f"주문내역 날짜 범위 지정에 실패했습니다: {e}") from e
        page.wait_for_timeout(2_000)

        # 진행 여부는 "수집한 주문 수"가 아니라 "받은 응답 수"로 판정한다 —
        # 페이지 경계에서 이미 본 주문만 담긴 페이지가 오면(정산내역에서
        # 실측된 겹침 현상) 주문 수는 그대로라 멀쩡히 넘어간 페이지를
        # "진행 없음 = 끝"으로 오판한다. `fetch_account_settlement`가 응답
        # 수를 쓰는 것과 같은 이유다.
        stop_reason = _click_next_page_until_done(
            page, lambda: state["matched_responses"], max_clicks=max_page_clicks,
        )
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminStatsScrapeError("주문내역 API 응답을 한 번도 확인하지 못했습니다")
    if not state["matched_responses"]:
        raise BaeminStatsScrapeError(
            f"주문내역 {start_date}~{end_date} 범위의 응답을 한 번도 받지 못했습니다"
        )

    if state["total_sizes"]:
        # 여러 페이지의 `totalSize`가 다를 수 있다(조회 도중 새 주문이
        # 들어오면 늘어난다) — 가장 작은 값을 기준으로 삼아, 조회 중
        # 늘어난 건수 때문에 false 에러가 나지 않게 한다.
        expected_total = min(state["total_sizes"])
        if len(collected) < expected_total:
            raise BaeminStatsScrapeError(
                f"주문내역 {start_date}~{end_date} 총 {expected_total}건 중 "
                f"{len(collected)}건만 수집했습니다(페이지네이션 종료 사유: {stop_reason}) "
                f"— 부분 수집을 저장하면 가장 오래된 구간이 영구 유실되므로 실패로 처리합니다"
            )

    return list(collected.values())


def _closest_ancestor_card(heading):
    """`heading`(날짜 헤딩 하나를 가리키는 구체적 로케이터)의 조상 `div`
    중에서, 날짜 헤딩(`_CARD_DATE_RE`) 매칭이 정확히 1개(자기 자신)인 가장
    가까운 것을 카드 컨테이너로 반환한다. 못 찾으면 `None`.

    배지("입금완료"/"입금예정") 텍스트에 의존하지 않는다(2026-08-13,
    `_click_all_settlement_cards_on_page` docstring "정정 3" 최종 수정 절
    참고 — 배지 문구가 없는 카드가 있다는 게 실 계정 라이브 검증에서
    새로 발견됐다). "헤딩 하나 = 카드 하나"라는 카드 컨테이너의 정의 자체를
    조건으로 쓰므로 배지 유무/문구와 무관하게 항상 성립한다.

    **알려진 제약 (2026-08-13, 최종 리뷰 사이클에서 발견, 미해결로 남김)**:
    `ancestors`는 문서 순서(최상위 조상부터)로 나오고, 이 루프는 앞에서부터
    훑어 "날짜 헤딩이 정확히 1개"인 첫 조상을 반환한다. 페이지에 카드가
    2개 이상이면 카드 경계에 도달하기 전까지는 다른 카드의 헤딩이 섞여
    개수가 1보다 크게 나와 정상 동작한다(실 계정 라이브 검증으로 20/20
    확인됨). 하지만 **그 페이지에 날짜 헤딩이 정확히 1개뿐이면**(30일 창
    안 정산배치 총 건수를 10으로 나눈 나머지가 1인 경우, 즉 페이지네이션
    마지막 페이지에 카드가 딱 1개만 남는 경우) 최상위 조상부터 이미 헤딩
    개수가 1이라 루프가 즉시 페이지 전체를 감싸는 거대한 wrapper div를
    "카드"로 잘못 반환한다 — 클릭 좌표가 완전히 엉뚱한 곳을 가리키게 된다.
    다행히 이 실패는 조용히 묻히지 않는다: `fetch_settlement_breakdown_details`의
    부분 캡처 방어 로직(요약 응답이 아는 배치 수와 실제로 잡힌 상세 응답
    수가 다르면 하드 에러)이 이 케이스에서 상세를 하나도 못 받는 결과를
    그대로 잡아내 `BaeminStatsScrapeError`를 던진다 — 그날의 정산 상세
    동기화만 실패로 기록되고(`_run_sync`의 기존 실패 격리 원칙대로 리뷰/
    매출/입금/재주문율/우가클은 영향 없음), 데이터가 조용히 틀리게 저장되는
    일은 없다. 현재 연결된 실 계정 데이터(30일 창에 배치 20건 = 정확히
    2페이지, 나머지 0)로는 이 케이스를 재현하지 못해 실측 수정이 보류됐다
    — 다음에 손댈 때는 "페이지에 날짜 헤딩이 정확히 1개"인 경우를 별도로
    분기해서 조상 탐색 자체를 건너뛰고(모호함이 없으므로) 그 헤딩이 속한
    카드를 직접 특정하는 방식으로 고쳐야 한다."""
    ancestors = heading.locator("xpath=ancestor::div")
    for j in range(ancestors.count()):
        candidate = ancestors.nth(j)
        if candidate.get_by_text(_CARD_DATE_RE, exact=True).count() == 1:
            return candidate
    return None


def _click_all_settlement_cards_on_page(page) -> None:
    """현재 페이지에 보이는 정산 배치 카드 전부를 순서대로 클릭해 각각의
    상세(`settle/history/details/{giveId}`) 응답을 끌어낸다. 카드는 "N월
    N일" 날짜 헤딩 텍스트를 포함하는 가장 안쪽 div로 특정한다(`has_text`
    필터는 조상 div까지 전부 매칭시키므로 `.last`로 가장 안쪽=카드 컨테이너를
    고른다) — 이 방식(카드 컨테이너 bounding box의 오른쪽 끝 클릭)이 실제
    상세 API 호출을 끌어내는 걸 실 계정으로 확인했다(2026-08-12, giveId
    531969790로 재현).

    **정정 (2026-08-12, Step 2 실 계정 검증 중 발견) — 상태 배지
    `.filter(has_text=re.compile(r"^입금(완료|예정)$"))`를 걷어냈다.** 원래
    브리프의 스타팅 코드는 날짜 헤딩으로 좁힌 뒤 이 정규식으로 한 번 더
    필터링했는데, 실 계정으로 돌려보니 카드 30개 중 단 하나도 클릭되지
    않고 `bounding_box()`가 전부 30초 타임아웃 났다. 원인은 정규식 자체 —
    Playwright의 `has_text=<regex>`는 그 정규식을 요소의 **전체
    텍스트 콘텐츠**에 대해 매칭하는데, `^...$` 앵커(re.MULTILINE 없음)는
    "요소 텍스트가 정확히 그 배지 문자열 하나뿐"인 경우에만 매칭된다. 그런데
    카드 컨테이너의 텍스트는 항상 날짜 헤딩·"음식배달"·정산기간·입금금액이
    함께 붙어있어 배지 텍스트 단독으로 존재하는 div가 애초에 없다 — 그래서
    이 필터는 계정 상태와 무관하게 상수적으로 빈 로케이터가 되고,
    `.last`가 영원히 리졸브되지 않았다. 진단 스크립트로 격리 재현해
    (`page.locator("div", has_text="8월 12일").filter(has_text=re.compile(...))`가
    7개 중 0개로 줄어드는 것을 직접 확인) 이 필터가 항상 0건이라는 걸
    확증했다. 날짜 헤딩 텍스트 자체가(이번 조사 계정 기준 30일 창 안에서는)
    유일했기 때문에 `has_text=heading_text` 필터 하나만으로 이미 카드
    컨테이너를 정확히 가장 안쪽 div로 좁힐 수 있었다(실측: 헤딩 10개 전부
    dedupe 후에도 10개, 중복 없음) — 그래서 배지 필터는 걷어내고 날짜 헤딩
    필터 + `.last`만 남겼다. 이 수정 뒤 여러 페이지에 걸친 카드 전부를
    빠짐없이 클릭해 상세 응답을 받는 것까지 실측 확인했다(정확한 건수는
    `fetch_settlement_breakdown_details` 아래 실측 결과 참고).

    **정정 3 (2026-08-13, 최종 리뷰 발견·수정 — 위 "알려진 미해결 한계"를
    구조적 포지셔널 선택자로 해결)**: 같은 날짜에 배치가 2건 이상이면
    날짜 헤딩 텍스트가 중복된다. 위 정정에서 남긴 `has_text=heading_text`
    + `.last` 방식은 텍스트 값으로 전역 검색을 하기 때문에, 헤딩이 2개면
    두 검색 모두 정확히 같은 카드(DOM상 더 뒤에 있는 쪽)로 리졸브된다 —
    `.last`가 "몇 번째 매칭인지"를 구분해주지 않기 때문에 앞 카드는
    영원히 클릭되지 않고 뒤 카드만 두 번 클릭된다.

    첫 시도는 "헤딩을 텍스트가 아니라 인덱스로 순회 + XPath `ancestor::`로
    그 헤딩 자신의 조상을 찾되, 조상 판별 조건은 기존과 동일하게
    '입금완료'/'입금예정' 배지 포함 div"였다. 이 접근은 실 계정 라이브
    검증(2026-08-13, 아래 `fetch_settlement_breakdown_details` 실측 결과
    참고)에서 20건 중 19건만 캡처되는 회귀를 냈다 — 원인은 리스트 맨 위
    "8월 13일"(당일, 아직 정산 진행 중인 배치로 추정) 카드 하나가
    "입금완료"/"입금예정" 배지 문구를 갖지 않아, 그 카드 자신을 조상으로
    가진 가장 가까운 매칭 div가 없어서 XPath가 훨씬 바깥의 훨씬 큰
    컨테이너(진단 스크립트로 측정한 bounding box 높이가 카드 1장의 148px가
    아니라 1624px — 카드 여러 장을 아우르는 리스트 래퍼)까지 타고 올라갔고,
    그 커진 박스의 "오른쪽 끝" 좌표를 클릭해도 실제 카드가 아닌 엉뚱한
    위치라 상세 응답이 발생하지 않았다(진단 스크립트로 재현·확인:
    `card_count=1, box={'height': 1624, ...}, clicked, details 0->0`).
    즉 배지 텍스트에 의존하는 조상 판별 조건 자체가 배지 문구가 없는
    카드(예: 당일 진행 중 배치)에 대해 깨지는 새로운 실패 모드였다.

    최종 수정은 배지 텍스트 의존을 완전히 제거했다 — 조상 판별 조건을
    "입금완료/입금예정 포함"이 아니라 **"날짜 헤딩(`_CARD_DATE_RE`) 매칭이
    정확히 1개(자기 자신)인 가장 가까운 조상 `div`"**로 바꿨다. 헤딩
    노드에서 `ancestor::div`로 모든 조상(가까운 순)을 얻은 뒤, 각 조상
    후보 안에서 `get_by_text(_CARD_DATE_RE, exact=True).count()`가 1이 되는
    첫 번째(=가장 가까운) 조상을 카드로 채택한다. 이 조건은 카드 안에 어떤
    상태 배지가 있든(있든 없든, 문구가 뭐든) 전혀 상관하지 않고, 오직 "이
    조상이 정확히 하나의 날짜 헤딩만 감싸는 가장 작은 div인가"만 본다 —
    카드 컨테이너의 정의 자체("헤딩 하나 = 카드 하나")와 정확히 일치한다.
    같은 날짜 중복 배치가 있어도 이 판정은 각 헤딩 노드 고유의 조상 축을
    타고 올라가므로(형제 카드의 서브트리는 애초에 조상이 아니다) 여전히
    서로 다른 카드로 정확히 갈라진다 — 텍스트 재검색이 아니라 heading이라는
    구체적 DOM 노드를 시작점으로 한 상대 탐색이라는 정정 3의 핵심
    아이디어는 유지하면서, 배지 문구 의존성만 제거한 형태다. 이 수정
    이후 재검증에서 20/20 전부 캡처되는 것을 실측 확인했다(아래
    `fetch_settlement_breakdown_details` 실측 결과 참고).

    **정정 2 (2026-08-12, 같은 검증에서 발견) — 카드 클릭이 "인라인 펼치기"가
    아니라 모달을 연다는 것과, 뷰포트 밖 카드는 아예 클릭되지 않는다는
    것.** 브리프의 스타팅 코드가 가정한 "카드 하나 클릭 → 즉시 정산상세
    모달이 뜬다"까지는 맞았지만, 리스트 전체가 "인라인으로 펼쳐지며 뒤 카드
    위치가 밀린다"는 위 docstring 첫 문단의 우려와 달리 실제로는 리스트
    자체는 전혀 움직이지 않고 화면 중앙에 완전히 별개의 `role="dialog"`
    모달(+ 풀스크린 backdrop)이 뜬다("정산내역 상세" 헤더, X 닫기 버튼,
    스크린샷으로 확인). 이 모달을 닫지 않고 바로 다음 카드 좌표를 클릭하면
    그 클릭은 리스트가 아니라 모달의 backdrop에 떨어져 아무 API 호출도
    일으키지 않는다(실측: 카드 0·1은 상세를 받았지만 2번째 카드부터 계속
    0건 — 모달이 열린 채로 이후 클릭이 전부 무효화됨). 그래서 각 카드
    클릭 뒤 `role="dialog"`가 남아있으면 Escape로 닫고 다음 카드로 넘어가야
    한다.

    모달을 닫아도 두 번째 문제가 남는다 — 로그인 세션의 뷰포트는
    1280×800(`baemin_auth.py`)인데 카드 한 장의 높이가 약 164px라 8번째
    카드부터는 y좌표가 800을 넘어 뷰포트 밖에 있다. `page.mouse.click`은
    뷰포트 좌표계를 쓰므로 뷰포트 밖 좌표를 클릭해도 아무 일도 일어나지
    않는다(실측: 스크롤 없이는 카드 0·1만 성공, 2번째부터는 dialog조차 안
    뜸). 그렇다고 카드마다 `scroll_into_view_if_needed()`로 스크롤하는 것도
    새 문제를 만든다 — 화면 우하단에는 스크롤과 무관하게 항상 고정 위치인
    "저에게 물어보세요" AI 챗봇 플로팅 버튼이 떠 있는데, 스크롤 후 특정
    카드의 "오른쪽 끝" 클릭 좌표가 뷰포트 우하단 근처로 오면 이 플로팅
    버튼과 겹쳐 그 챗봇이 열려버린다(스크린샷으로 확인 — "무엇이든
    물어보세요" AI 챗봇 다이얼로그가 뜸). 이 챗봇 다이얼로그는 Escape로
    닫히지 않아 그 뒤로는 영구적으로 클릭이 막힌다(실측: 카드 2부터 끝까지
    전부 dialog count가 1로 고정, 새 상세 응답 0건).

    두 문제를 한 번에 해결하는 방법으로 스크롤 자체를 없앴다 —
    `fetch_settlement_breakdown_details`가 카드 클릭 루프에 들어가기 전에
    `page.set_viewport_size`로 뷰포트를 세로로 넉넉하게(3000px) 키운다.
    카드가 전부 한 화면 안에 이미 렌더링돼 있으므로 스크롤이 아예 필요
    없어지고, 고정 위치 챗봇 버튼도 그만큼 커진 뷰포트의 맨 아래(카드
    리스트보다 한참 아래)로 밀려나 더 이상 카드 클릭 좌표와 겹치지 않는다.
    이 방식으로 실 계정 30일 창(2026-08-12 기준)의 카드 20개 전부(페이지당
    10개씩 2페이지)를 빠짐없이 클릭해 상세 응답 20건을 받는 것을 실측
    확인했다(`fetch_settlement_breakdown_details` 아래 정확한 실측 결과
    참고)."""
    headings = page.get_by_text(_CARD_DATE_RE, exact=True)
    for i in range(headings.count()):
        # 인덱스로 리졸브한 heading 하나(구체적 DOM 노드 참조) — 텍스트
        # 값으로 다시 검색하지 않으므로 다른 헤딩과 텍스트가 겹쳐도 섞이지
        # 않는다(위 "정정 3" 참고).
        heading = headings.nth(i)
        card = _closest_ancestor_card(heading)
        if card is None:
            continue
        box = card.bounding_box()
        if box is None:
            continue
        page.mouse.click(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
        page.wait_for_timeout(1_200)
        # 카드 클릭은 인라인 펼치기가 아니라 별도 모달을 연다(위 "정정 2"
        # 참고) — 다음 카드 클릭이 이 모달의 backdrop에 흡수되지 않도록
        # 반드시 닫고 넘어간다.
        if page.get_by_role("dialog").count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)


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
    반환하고, `map_settlement_breakdown_by_date`가 그런 항목을 건너뛴다.

    **실 계정 검증 결과 (2026-08-12, Step 2)**: `today - 30일` ~ `today`
    범위로 호출해 상세 응답 20건(2026-07-13 ~ 2026-08-12, 페이지당 10개씩
    2페이지)을 전부 받았고, `depositDueDate` 매핑도 20/20 전부 성공했다
    (미매핑 0건). `giveId` 기준으로도 20개 전부 유일해 페이지 경계 중복도
    없었다. 날짜별 4개 카테고리 합산 값도 전부 0 이상의 합리적인 금액으로
    나왔다(예: 2026-08-12 커미션 131,402원 — Task 2의 giveId 531969790
    fixture와 정확히 일치, 같은 배치를 이번엔 화면 클릭 경로로 재확인한
    셈).

    **재검증 (2026-08-13, 최종 리뷰 fix round)**: 카드 선택자를 배지
    텍스트 기반 → 인덱스 순회 XPath ancestor로 바꾼 직후 첫 라이브
    재검증에서는 20건 중 19건만 캡처되는 회귀가 새로 나왔다(위
    `_click_all_settlement_cards_on_page` docstring "정정 3" 참고 — 당일
    진행 중인 배치 카드가 배지 문구를 갖지 않아 조상 선택이 엉뚱하게 큰
    컨테이너로 튀는 문제, 아래 신규 부분-캡처 하드 에러가 실제로 이걸
    잡아냈다). 배지 의존을 완전히 제거한 `_closest_ancestor_card`
    (날짜 헤딩 매칭이 정확히 1개인 가장 가까운 조상)로 다시 고친 뒤
    재검증하니 `today - 30일` ~ `today` 범위에서 20건 전부(2026-07-14 ~
    2026-08-13, 당일 진행 중 배치 포함) 정확히 캡처됐고, 2026-08-12 값도
    위와 동일하게 재확인됐다 — 새로 추가한 부분-캡처 하드 에러(아래)가
    건강한 경로에서 false-trigger하지 않는 것까지 이 재검증으로 함께
    확인했다."""
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
                # `**body`를 마지막에 두면 body가 만약 top-level `giveId`/
                # `depositDueDate` 키를 갖고 있을 경우 위에서 명시한 값을
                # 조용히 덮어쓸 수 있다 — 하지만 Task 2에서 실측한 원본
                # 상세 응답의 실제 top-level 키는 `giveAmount`/
                # `baemin1Details`/`baeminDetails`/`etcDetails`/`cpcDetails`
                # 뿐이라(`_SETTLE_DETAIL_531969790` fixture 참고) 오늘
                # 기준으로는 충돌이 없다 — 배민이 나중에 응답 스키마를
                # 바꿔 이 키들을 추가하면 재검토가 필요하다.
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

        # 카드 클릭 루프 동안만 뷰포트를 늘린다(모듈 상단 상수 주석,
        # `_click_all_settlement_cards_on_page` docstring "정정 2" 참고) —
        # 끝나면 반드시 원래 크기로 되돌린다(같은 `page`가 로그인 세션
        # 전체에서 재사용되므로, 다른 화면 조작 로직이 기본 뷰포트 크기를
        # 가정하고 있을 수 있다).
        # `page.viewport_size`가 None일 수 있는 경우(예: 뷰포트 없이 붙은
        # CDP 세션)를 대비해 이 모듈이 로그인 시 실제로 쓰는 기본값
        # (`baemin_auth.py`, 1280x800)으로 방어적으로 대체한다 — 이 리포지토리의
        # 실제 사용 경로(항상 `baemin_auth.login()`이 만든 세션)에서는
        # 재현되지 않았지만, 그 경로를 벗어나면 `original_viewport["width"]`가
        # `NoneType`에 인덱싱을 시도해 크래시할 수 있었다.
        original_viewport = page.viewport_size or {"width": 1280, "height": 800}
        page.set_viewport_size({"width": original_viewport["width"], "height": _CARD_CLICK_VIEWPORT_HEIGHT})
        try:
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
            page.set_viewport_size(original_viewport)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminStatsScrapeError("정산 상세 API 응답을 한 번도 확인하지 못했습니다")

    # `observed_any`는 summary 엔드포인트(그리고 날짜 범위 다이얼로그)가
    # 정상 동작했다는 것만 증명한다 — 카드 클릭이 실제로 상세 응답을
    # 끌어냈는지, 그것도 배치 개수만큼 전부 끌어냈는지는 별개다.
    # `fetch_shop_stats`의 `_should_count_sales_response` 도입 배경(코드
    # 리뷰로 발견된 은폐 실패 경로, 모듈 내 해당 함수 docstring 참고)과 같은
    # 종류의 문제 — 카드 클릭 선택자가 다른 계정/DOM 변형/배민 UI 변경으로
    # 깨지면 `details`가 조용히 빈 리스트로 남거나 일부만 채워지는데, 둘 다
    # 호출자 입장에서 "이번 창에는 정산 배치가 진짜 0건"이거나 "정상적으로
    # 전부 캡처됨"과 구분이 안 된다. 그래서 `give_id_to_date`(summary에서
    # 만든, 이 창 안의 배치 전체 목록)의 unique 개수와 `details`에서 실제로
    # 캡처한 unique giveId 개수를 비교한다 — 완전히 0건(구 버전부터 있던
    # 하드 에러, 아래 메시지 그대로 유지)과 일부만 캡처된 부분 실패(2026-08-13
    # 추가, defense-in-depth — 같은 날짜에 배치가 2건 이상일 때 텍스트 기반
    # 카드 선택자가 한쪽만 골라버리던 버그를 `_click_all_settlement_cards_on_page`
    # 쪽에서 포지셔널 선택자로 고쳤지만, 그 수정이 나중에 다시 깨지거나
    # 다른 원인으로 일부 카드만 클릭이 누락되는 경우까지 폭넓게 잡기 위한
    # 게이트다) 둘 다 여기서 하드 에러로 표면화한다. 배치 자체가 0건이면
    # (정산 이력이 진짜 없는 정상 케이스) `give_id_to_date`도 비어있어 이
    # 블록 전체를 건너뛴다.
    if give_id_to_date:
        unique_captured = len({d["giveId"] for d in details})
        total_batches = len(give_id_to_date)
        if unique_captured == 0:
            raise BaeminStatsScrapeError(
                "정산 배치는 있었지만 카드 클릭으로 상세 응답을 하나도 받지 못했습니다"
            )
        if unique_captured < total_batches:
            raise BaeminStatsScrapeError(
                f"정산 배치 {total_batches}건 중 상세 응답 {unique_captured}건만 받았습니다"
            )

    return details


def fetch_shop_info(page, shop_no: str) -> dict:
    """사장님광장 가게관리 화면(`/shops/{shop_no}/manage`)에서
    `GET /v4/store/shops/{shop_no}` organic 응답을 가로채 상호명·카테고리·
    도로명주소·위도·경도를 반환한다. 광고 순위 실측 크롤러(`crawler/`)가
    가게 정보를 `.env` 수동 편집 대신 이 함수의 결과로 받을 수 있게
    하기 위한 용도다(호출자는 `backend/app/routers/ads.py`의
    `_run_local_crawl` 참고).

    반환 키: `name`(str), `category`(str, 배민 카테고리 탭과 정확히
    일치하는 문자열), `road_address`(str), `latitude`(float),
    `longitude`(float).

    실 계정 재검증(2026-08-15, 치밥대장 `shop_no='14804318'` 기준) 결과
    브리프의 스타팅 코드는 수정 없이 그대로 통과했다 — `name`/
    `categories[0].name`/`address.road.address`/`address.latitude`/
    `address.longitude` 응답 형태가 계획 수립 단계의 조사 결과와 정확히
    일치했다(정정 사항 없음).

    **정정 (2026-08-15, 코드 리뷰 발견)**: 반환 dict를 만드는 구간의
    `except (KeyError, IndexError)`가 `TypeError`를 못 잡았다. 도로명주소가
    없는(지번주소만 있는) 매장은 `address["road"]`가 `None`일 수 있고,
    좌표가 없는 매장은 `address["latitude"]`/`address["longitude"]`가
    `None`일 수 있는데, 그 경우 `None["address"]` 인덱싱이나 `float(None)`이
    `KeyError`/`IndexError`가 아니라 `TypeError`를 던져 이 함수가 보장해야
    할 "응답은 받았지만 파싱 실패 시 `BaeminStatsScrapeError`" 계약이
    깨졌다. 치밥대장은 도로명주소·좌표가 전부 채워져 있어 Step 2 라이브
    검증에서는 이 경로가 재현되지 않았다(리뷰로 발견, 실 계정 재현은 아님).
    `TypeError`를 같은 except 절에 추가해 null 필드도 다른 파싱 실패와
    동일하게 `BaeminStatsScrapeError`로 표면화하도록 고쳤다."""
    state = {"observed_any": False, "body": None}

    def _on_response(response) -> None:
        url = response.url
        if "self-api.baemin.com" not in url:
            return
        if urlparse(url).path != f"/v4/store/shops/{shop_no}":
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
            page.goto(f"https://self.baemin.com/shops/{shop_no}/manage")
        except Exception as e:
            raise BaeminStatsScrapeError(f"가게관리 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)
        _dismiss_backdrop_if_present(page)
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminStatsScrapeError("가게 정보 API 응답을 한 번도 확인하지 못했습니다")
    if state["body"] is None:
        raise BaeminStatsScrapeError("가게 정보 API 응답을 받았지만 파싱하지 못했습니다")

    body = state["body"]
    try:
        categories = body["categories"]
        address = body["address"]
        return {
            "name": body["name"],
            "category": categories[0]["name"],
            "road_address": address["road"]["address"],
            "latitude": float(address["latitude"]),
            "longitude": float(address["longitude"]),
        }
    except (KeyError, IndexError, TypeError) as e:
        raise BaeminStatsScrapeError(f"가게 정보 응답 형태가 예상과 다릅니다: {e}") from e
