# Task 2 검증 스파이크 결과

- 날짜: 2026-07-27
- 검증 대상: 배달의민족 앱의 카테고리 순위 리스트가 Appium accessibility
  tree(`driver.page_source` = uiautomator dump XML)로 읽히는가?

## 결론: **PASS** — accessibility tree 파싱 방식(스펙의 1차 접근법)으로 진행 가능

## 환경 확인

- 앱 패키지명: **`com.sampleapp`** (설치 후 `adb shell pm list packages | grep -i baemin`
  으로는 안 잡히고, 실제로는 이 패키지명으로 설치돼 있었다 — 배민의 실제 앱
  ID가 사전 노출을 막기 위해 임의 패키지명을 쓰는 것으로 보인다.
  `config.py`의 `BAEMIN_PACKAGE_NAME` 상수 값으로 이 값을 쓴다.)
- 런처 액티비티: `com.sampleapp/.AppIconCustomV1`
  (`adb shell cmd package resolve-activity --brief com.sampleapp` 로 확인)
- 앱 실행 명령: `adb shell am start -n com.sampleapp/.AppIconCustomV1`
  (`monkey -p com.sampleapp -c android.intent.category.LAUNCHER 1` 은
  이 앱에서 포그라운드 전환이 안 됨 — 반드시 `am start -n`으로 실행할 것)
- 에뮬레이터 시스템 로케일을 한국어로 바꿔야 카테고리/가게 문구가 한국어로
  나온다. `google_apis_playstore` 이미지는 non-rooted라
  `adb shell am broadcast -a android.intent.action.LOCALE_CHANGED`가
  `SecurityException`으로 거부된다. 대신:
  ```
  adb shell settings put system system_locales ko-KR
  adb reboot
  adb wait-for-device
  ```
  재부팅 후에만 로케일이 실제로 적용된다(브로드캐스트로는 즉시 적용 안 됨).

## 카테고리 화면까지 도달하는 탭 순서

1. 앱 홈 화면 (로그인 없이 진입 가능 — 하단 첫 화면이 곧 홈)
2. 홈 화면의 "음식배달" 탭(이미 기본 선택돼 있음) 아래 카테고리 아이콘
   그리드에서 원하는 카테고리 아이콘을 탭한다 (예: "피자" 아이콘).
   - **주의**: 카테고리 아이콘 그리드의 이미지가 아직 로딩되기 전에 탭하면
     레이아웃이 달라 엉뚱한 요소(신규가입 유도 배너 등)가 눌릴 수 있다.
     이미지 로딩 완료 후 탭할 것. `baemin_navigator.py`에서는 좌표 탭이
     아니라 accessibility tree에서 `text="피자"` 노드를 찾아 그 노드를
     탭하는 방식(요소 기반)으로 구현해야 좌표 오차 문제가 없다.
3. 카테고리 화면 진입 시 상단에 카테고리 탭 바(치킨/중식/돈까스·회/피자/
   패스트푸드/찜·탕/...)가 나타나고, 방금 탭한 카테고리가 자동 선택된
   상태로 로딩된다 (스피너 표시 → 약 2-3초 후 리스트 로드 완료).
4. 로딩이 끝나면 상단에 "최소주문금액 없는 한그릇" 같은 가로 스크롤
   추천 캐러셀이 먼저 나오고, 그 아래 "기본순" 정렬 옵션과 세로 리스트
   (실제 순위 리스트)가 이어진다. **순위 판별 대상은 이 "기본순" 세로
   리스트다** — 캐러셀은 별도 추천 영역이라 순위에 포함하지 않는다.

## Accessibility Tree에서 확인한 실제 속성

- **가게명**: `text` 속성과 `content-desc` 속성 둘 다에 그대로 노출된다.
  예: `text="미친피자 노원직영점"`, `content-desc="미친피자 노원직영점"`.
  → `rank_finder.py`는 `text` 속성 우선, 없으면 `content-desc`를 확인한다.
- **광고 배지**: `text="광고"`로는 노출되지 않는다. 대신 별도 View 노드의
  `content-desc="추천 광고 영역"`으로 노출된다:
  ```xml
  <node index="5" text="" resource-id="" class="android.view.View"
        package="com.sampleapp" content-desc="추천 광고 영역"
        clickable="false" bounds="[945,2036][1041,2086]" />
  ```
  → `is_ad` 판별 로직은 `content-desc`에 `"광고"`라는 문자열이 포함된
  노드가 있는지로 판단한다 (정확히 `"추천 광고 영역"`이 아니라 부분
  문자열 `"광고"` 포함 여부로 체크 — 문구가 버전마다 조금 바뀔 수 있음).
  이 노드는 가게 카드 블록 내부, 가게명 노드와 `bounds` y좌표가 근접한
  위치에 나타나므로, 화면 세로 위치(`bounds`) 순서로 정렬한 뒤 가장
  가까운 가게명 카드에 속한 것으로 매칭한다.
- 그 외 참고용으로 노출되는 속성: `content-desc="별점 4.9점, 리뷰 4,487개"`
  (평점/리뷰수), `content-desc="거리 1.6km"` (거리).
- 카테고리 탭 텍스트도 그대로 노출된다: `text="치킨"`, `text="피자"` 등
  → `CATEGORY_LABEL` 매칭에 그대로 사용 가능.

## 스크롤/페이징

- 이번 스파이크에서는 스크롤 동작까지는 검증하지 않았다(다음 태스크인
  `baemin_navigator.py`의 `scroll_and_collect`에서 실제 스크롤 후 새
  항목이 나타나는지, 동일 가게가 중복 노드로 잡히는지를 통합 검증 단계에서
  확인한다). 다만 화면 진입 시점에 이미 여러 가게 카드가 개별 노드로
  잡히는 것은 확인했다 — 트리 구조 자체는 리스트 아이템마다 반복되는
  형태다.

## 산출물

- `output/debug/sample_category_page_source.xml` — 이 스파이크에서 실제로
  덤프한 원본 accessibility tree (피자 카테고리, 기본순 리스트, 첫 화면
  기준). `rank_finder.py`의 pytest fixture로 사용한다.
