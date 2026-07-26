# Appium 배민 카테고리 순위 실측 크롤러 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배민 앱에서 내 가게 기준 1/2/3/4km 반경 지점마다 특정 카테고리 순위를
실측해 스크린샷+CSV로 남기는 독립 실행 도구를 만든다.

**Architecture:** 저장소 루트의 `crawler/` 폴더(메인 프로젝트와 분리)에
Python 스크립트로 구현. Appium이 macOS 위 Android 에뮬레이터를 조작하고,
좌표 계산(geo_sampling)·카카오 지오코딩(geocode)·화면 파싱(rank_finder)은
네트워크/Appium에 의존하지 않는 순수 함수로 분리해 pytest로 검증한다.

**Tech Stack:** Python 3, Appium-Python-Client 3.x (Appium 서버 2.x),
Android 커맨드라인 툴 + 에뮬레이터, python-dotenv, requests, pytest.

## Global Constraints

- 이 도구는 메인 프로젝트(`backend/`, `frontend/`, `schema.sql`)를 전혀
  건드리지 않는다. 전부 `crawler/` 아래에만 파일을 만든다.
- 개인적·1회성 테스트 목적이다. 상시 자동 수집 서비스로 만들지 않는다.
- 배민은 로그인 없이 가게 목록을 볼 수 있다 — 로그인/세션/자격증명 자동화는
  범위에 없다.
- `순위`는 화면에 보이는 전체 노출 순서 그대로(광고 포함) 센다. 각 항목이
  광고인지 여부는 `is_ad` 컬럼으로 별도 기록한다.
- 반경 샘플링은 1/2/3/4km 각각 랜덤 방위각 1개 — 기준점(가게) 포함 총 5개
  지점.
- 결과는 지점당 스크린샷 1장 + `results.csv` 한 줄로 남긴다.
- `CATEGORY_LABEL`, `STORE_DISPLAY_NAME`은 실제 앱 화면 문구와 정확히
  일치해야 하며, 스크립트가 추측하지 않는다 — `.env`에 사람이 직접 입력한다.
- 카카오 로컬 API 키는 사용자가 카카오 개발자 사이트에서 직접 발급받아
  `.env`에 넣는다 — 이 계획의 어떤 태스크도 API 키를 대신 발급하거나
  자격증명을 다루지 않는다.
- **Task 2는 사람이 직접 조작해야 하는 구간을 포함한다** (Google 계정
  로그인, Play스토어 앱 설치). 이 구간은 에이전트가 자동화하지 않고,
  사용자에게 안내 후 완료를 기다린다.

---

### Task 1: 프로젝트 스캐폴드 + Android/Appium 환경 구축 + 스모크 테스트

**Files:**
- Create: `crawler/requirements.txt`
- Create: `crawler/.env.example`
- Create: `crawler/.gitignore`
- Create: `crawler/config.py`
- Create: `crawler/smoke_test.py`
- Create: `crawler/README.md`

**Interfaces:**
- Produces: `config.Settings` 데이터클래스 — `store_address: str`,
  `store_display_name: str`, `category_label: str`, `kakao_api_key: str`,
  `RADII_KM: list[int] = [1, 2, 3, 4]` (모듈 상수). `config.load_settings() -> Settings`
  함수가 `.env`를 읽어 반환.

- [ ] **Step 1: 디렉터리와 기본 파일 작성**

`crawler/requirements.txt`:
```
Appium-Python-Client>=3.1.0
python-dotenv>=1.0.0
requests>=2.31.0
pytest>=8.0.0
```

`crawler/.env.example`:
```
KAKAO_REST_API_KEY=여기에_카카오_로컬_API_REST_키
STORE_ADDRESS=서울시 노원구 당고개로 1
STORE_DISPLAY_NAME=치킨대장 당고점
CATEGORY_LABEL=치킨
```

`crawler/.gitignore`:
```
.env
output/
.venv/
__pycache__/
```

`crawler/config.py`:
```python
"""crawler 전역 설정. .env에서 읽어온다 — 값을 스크립트가 추측하지 않는다."""

from dataclasses import dataclass

from dotenv import dotenv_values

RADII_KM = [1, 2, 3, 4]


@dataclass(frozen=True)
class Settings:
    kakao_api_key: str
    store_address: str
    store_display_name: str
    category_label: str


def load_settings(env_path: str = ".env") -> Settings:
    values = dotenv_values(env_path)
    missing = [
        k for k in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL")
        if not values.get(k)
    ]
    if missing:
        raise RuntimeError(f".env에 다음 값이 없습니다: {', '.join(missing)}")
    return Settings(
        kakao_api_key=values["KAKAO_REST_API_KEY"],
        store_address=values["STORE_ADDRESS"],
        store_display_name=values["STORE_DISPLAY_NAME"],
        category_label=values["CATEGORY_LABEL"],
    )
```

`crawler/README.md`:
```markdown
# 배민 카테고리 순위 실측 크롤러

메인 프로젝트(review-doctor)와 분리된 독립 도구. 내 가게 기준 1/2/3/4km
반경 지점에서 배민 카테고리 순위를 실제로 측정한다. 1회성 테스트 목적이며
상시 자동 수집이 아니다.

## 준비물

1. `.env.example`을 `.env`로 복사하고 값을 채운다 (카카오 REST API 키는
   https://developers.kakao.com 에서 애플리케이션 등록 후 발급받는다).
2. Android SDK/에뮬레이터/Appium 환경은 아래 "환경 구축" 참고.
3. 에뮬레이터에 배달의민족 앱을 Play스토어에서 직접 설치한다 (수동, 아래 참고).

## 환경 구축

(Task 1에서 상세 커맨드로 채워짐)

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_crawl.py
```

결과는 `output/results.csv`와 `output/screenshots/`에 저장된다.
```

- [ ] **Step 2: Android 커맨드라인 툴 + 에뮬레이터 설치**

Run:
```bash
brew install openjdk@17
brew install --cask android-commandlinetools
```

`~/.zshrc` 또는 현재 셸에 추가:
```bash
export ANDROID_HOME="$(brew --prefix)/share/android-commandlinetools"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"
```

Run (라이선스 동의 + 필수 패키지):
```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "platforms;android-34"
```

시스템 이미지는 Mac 아키텍처에 맞춰 설치 (Apple Silicon vs Intel):
```bash
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
  IMAGE="system-images;android-34;google_apis_playstore;arm64-v8a"
else
  IMAGE="system-images;android-34;google_apis_playstore;x86_64"
fi
sdkmanager "$IMAGE"
echo "no" | avdmanager create avd -n baemin_test -k "$IMAGE" --device "pixel_6"
```

Expected: `avdmanager list avd`에 `baemin_test`가 나온다.

- [ ] **Step 3: 에뮬레이터 부팅 확인 (창이 보이는 상태로 — Task 2에서 사람이 조작해야 함)**

Run:
```bash
emulator -avd baemin_test &
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
adb devices
```

Expected: `adb devices`에 `emulator-5554	device`가 나오고, 실제 에뮬레이터
창이 화면에 떠 있다 (`-no-window` 쓰지 않음 — 사람이 볼 수 있어야 함).

- [ ] **Step 4: Appium 서버 + uiautomator2 드라이버 설치**

Run:
```bash
npm install -g appium
appium driver install uiautomator2
appium &
sleep 3
curl -s http://localhost:4723/status
```

Expected: `curl` 응답에 `"ready":true` 포함.

- [ ] **Step 5: 파이썬 가상환경 + 의존성 설치**

Run:
```bash
cd crawler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 6: 스모크 테스트 — Appium이 에뮬레이터를 실제로 조작하는지 확인**

`crawler/smoke_test.py`:
```python
"""Appium 서버가 에뮬레이터를 제어할 수 있는지 확인하는 스모크 테스트.
배민 앱 없이 안드로이드 기본 설정 앱으로 검증한다."""

from appium import webdriver
from appium.options.android import UiAutomator2Options


def main():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = "com.android.settings"
    options.app_activity = ".Settings"
    options.no_reset = True

    driver = webdriver.Remote("http://localhost:4723", options=options)
    try:
        assert len(driver.page_source) > 100, "page_source가 비어 있음"
        print("OK: Appium이 에뮬레이터를 정상적으로 조작합니다.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
```

Run: `crawler/.venv/bin/python crawler/smoke_test.py`
Expected: `OK: Appium이 에뮬레이터를 정상적으로 조작합니다.` 출력.

- [ ] **Step 7: Commit**

```bash
git add crawler/
git commit -m "feat: crawler 스캐폴드 + Android/Appium 환경 구축 + 스모크 테스트

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 배민 앱 설치(사람) + accessibility tree 검증 스파이크

**이 태스크는 사람이 직접 조작해야 하는 구간으로 시작한다. 이 부분은
자동화하지 않는다.**

**Files:**
- Create: `crawler/spike_explore.py`
- Create: `crawler/output/debug/sample_category_page_source.xml` (스파이크
  성공 시 생성되는 실측 데이터)
- Create: `crawler/output/debug/SPIKE_NOTES.md`

**Interfaces:**
- Produces: `sample_category_page_source.xml` — Task 5(rank_finder.py)의
  테스트 픽스처. `SPIKE_NOTES.md` — Task 6(baemin_navigator.py)이 그대로
  쓸 실제 앱 패키지명·카테고리 탭 진입 절차 기록.

- [ ] **Step 1: (사람) Google 로그인 + 배민 앱 설치**

에뮬레이터 창(Task 1에서 띄운 `baemin_test`)에서 직접:
1. 설정 → 계정 → Google 로그인 (본인 Google 계정)
2. Play 스토어 열어서 "배달의민족" 검색 → 설치
3. 앱을 한 번 실행해서 위치 권한 요청이 뜨면 "앱 사용 중에만 허용" 선택
4. 위치 관련 온보딩 화면이 있으면 대충 넘겨서 가게 목록이 보이는 홈 화면까지 도달

완료되면 다음 단계로 진행한다.

- [ ] **Step 2: 패키지명 확인**

Run: `adb shell pm list packages | grep -i "baemin\|woowa\|sampleapp"`
Expected: 배민 앱의 패키지명이 출력됨 (예: `package com.xxx.yyy`). 이 값을
`SPIKE_NOTES.md`에 기록한다 — 정확한 문자열은 실제 출력에서 그대로 복사한다.

- [ ] **Step 3: 탐색 스크립트로 카테고리 화면까지 이동, 화면 캡처하며 조작**

`crawler/spike_explore.py`:
```python
"""배민 앱의 accessibility tree가 읽히는지 확인하는 탐색 스크립트.
실행하면서 output/debug/step_N.png 스크린샷을 남기고, Read 도구로 확인해
다음 탭 좌표를 정하는 방식으로 대화형으로 사용한다."""

import sys

from appium import webdriver
from appium.options.android import UiAutomator2Options

PACKAGE = "여기에_Step_2에서_확인한_패키지명"  # SPIKE_NOTES.md 확인 후 채움


def get_driver():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = PACKAGE
    options.no_reset = True
    return webdriver.Remote("http://localhost:4723", options=options)


def screenshot(driver, name):
    path = f"output/debug/{name}.png"
    driver.save_screenshot(path)
    print(f"저장: {path}")


def tap(driver, x, y):
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})


if __name__ == "__main__":
    driver = get_driver()
    action = sys.argv[1] if len(sys.argv) > 1 else "screenshot"
    if action == "screenshot":
        screenshot(driver, "current")
    elif action == "tap":
        tap(driver, int(sys.argv[2]), int(sys.argv[3]))
        screenshot(driver, "after_tap")
    elif action == "dump":
        with open("output/debug/current_page_source.xml", "w") as f:
            f.write(driver.page_source)
        print("저장: output/debug/current_page_source.xml")
    driver.quit()
```

사용법 (반복 실행하며 카테고리 화면까지 이동):
```bash
mkdir -p output/debug
.venv/bin/python spike_explore.py screenshot   # 현재 화면 캡처
# Read 도구로 output/debug/current.png를 본 뒤, 탭할 좌표를 결정
.venv/bin/python spike_explore.py tap 540 1200  # 예: 카테고리 탭 위치
.venv/bin/python spike_explore.py screenshot    # 결과 확인, 반복
```

카테고리(예: 치킨) 목록 화면에 도달하면:
```bash
.venv/bin/python spike_explore.py dump
cp output/debug/current_page_source.xml output/debug/sample_category_page_source.xml
```

- [ ] **Step 4: accessibility tree에 가게명 텍스트가 실제로 잡히는지 확인**

Run:
```bash
grep -o 'text="[^"]*"' output/debug/sample_category_page_source.xml | head -30
```

**판정 기준:**
- 가게 이름으로 보이는 문자열들이 `text="..."` 형태로 다수 보이면 → **성공**.
  Task 3부터 계속 진행한다.
- `text=""`뿐이거나 가게명이 전혀 안 보이면(커스텀 렌더링으로 텍스트가
  트리에 노출 안 되는 경우) → **실패**. 이 경우 이 태스크를
  DONE_WITH_CONCERNS로 보고하고, 컨트롤러에게 OCR 대안 검토를 요청한다
  (스펙의 "화면 파싱 방식 대안 2번" 참고 — 이번 계획 범위 밖이므로 새 계획이
  필요하다).

- [ ] **Step 5: SPIKE_NOTES.md 작성**

`crawler/output/debug/SPIKE_NOTES.md`:
```markdown
# 스파이크 결과

- 배민 앱 패키지명: (Step 2 결과 그대로 기록)
- accessibility tree 판정: 성공 / 실패 (Step 4 결과)
- 카테고리 화면까지 도달한 탭 시퀀스: (예: 홈 화면 → (540,1200) 탭 →
  카테고리 리스트 → "치킨" 탭(좌표: ...))
- 가게명 텍스트가 위치한 XML 노드 패턴: (예: `<android.widget.TextView
  text="..." resource-id="com.xxx:id/store_name">` 처럼 실제 태그/속성
  이름을 기록 — Task 5가 이걸 그대로 파싱한다)
- 광고 배지 노드 패턴: (예: `resource-id="com.xxx:id/ad_badge"` 존재 여부로
  판별 — 실제 관찰된 속성명 기록)
```

- [ ] **Step 6: Commit**

```bash
git add crawler/spike_explore.py crawler/output/debug/
git commit -m "feat: 배민 앱 accessibility tree 검증 스파이크 — 실측 XML 픽스처 확보

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: geo_sampling.py — 반경 좌표 계산 (TDD)

**Files:**
- Create: `crawler/geo_sampling.py`
- Test: `crawler/tests/test_geo_sampling.py`

**Interfaces:**
- Produces: `geo_sampling.destination_point(lat, lng, bearing_deg, distance_km) -> tuple[float, float]`,
  `geo_sampling.sample_points(base_lat, base_lng, radii_km, rng=None) -> list[dict]`
  — 각 dict는 `{"radius_km": int, "bearing_deg": float, "lat": float, "lng": float}`.
  `rng`는 `random.Random` 인스턴스(생략 시 기본 `random.Random()`) — 테스트에서
  시드 고정으로 결정적 검증.

- [ ] **Step 1: 실패하는 테스트 작성**

`crawler/tests/test_geo_sampling.py`:
```python
import math
import random

from geo_sampling import destination_point, sample_points


def test_destination_point_north_1km():
    # 정북(0도)으로 1km 이동하면 위도만 증가하고 경도는 거의 그대로여야 한다
    lat, lng = destination_point(37.6542, 127.0620, bearing_deg=0, distance_km=1)
    assert lat > 37.6542
    assert math.isclose(lng, 127.0620, abs_tol=0.001)


def test_destination_point_distance_is_correct():
    # 임의 방향으로 2km 이동한 실제 거리를 haversine 역계산으로 검증
    lat, lng = destination_point(37.6542, 127.0620, bearing_deg=45, distance_km=2)

    R = 6371.0
    lat1, lng1, lat2, lng2 = map(math.radians, (37.6542, 127.0620, lat, lng))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    computed_km = 2 * R * math.asin(math.sqrt(a))
    assert math.isclose(computed_km, 2, abs_tol=0.01)


def test_sample_points_returns_one_point_per_radius():
    points = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(42))
    assert [p["radius_km"] for p in points] == [1, 2, 3, 4]
    assert all(0 <= p["bearing_deg"] < 360 for p in points)


def test_sample_points_deterministic_with_same_seed():
    a = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(7))
    b = sample_points(37.6542, 127.0620, radii_km=[1, 2, 3, 4], rng=random.Random(7))
    assert a == b
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd crawler && .venv/bin/python -m pytest tests/test_geo_sampling.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'geo_sampling'`)

- [ ] **Step 3: 구현**

`crawler/geo_sampling.py`:
```python
"""기준 좌표 + 반경 목록 → 반경별 랜덤 방위각 좌표. 네트워크/Appium 의존 없음."""

import math
import random

EARTH_RADIUS_KM = 6371.0


def destination_point(lat: float, lng: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """구면 삼각법 destination-point 공식."""
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    bearing = math.radians(bearing_deg)
    angular_dist = distance_km / EARTH_RADIUS_KM

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_dist) + math.cos(lat1) * math.sin(angular_dist) * math.cos(bearing)
    )
    lng2 = lng1 + math.atan2(
        math.sin(bearing) * math.sin(angular_dist) * math.cos(lat1),
        math.cos(angular_dist) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lng2)


def sample_points(base_lat: float, base_lng: float, radii_km: list[int], rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random()
    points = []
    for radius_km in radii_km:
        bearing_deg = rng.uniform(0, 360)
        lat, lng = destination_point(base_lat, base_lng, bearing_deg, radius_km)
        points.append({"radius_km": radius_km, "bearing_deg": round(bearing_deg, 2), "lat": lat, "lng": lng})
    return points
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd crawler && .venv/bin/python -m pytest tests/test_geo_sampling.py -v`
Expected: PASS 4건

- [ ] **Step 5: Commit**

```bash
git add crawler/geo_sampling.py crawler/tests/test_geo_sampling.py
git commit -m "feat: geo_sampling.py — 반경별 랜덤 좌표 계산 (TDD)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: geocode.py — 카카오 로컬 API 주소 변환 (TDD, 네트워크 모킹)

**Files:**
- Create: `crawler/geocode.py`
- Test: `crawler/tests/test_geocode.py`

**Interfaces:**
- Consumes: 없음 (독립).
- Produces: `geocode.address_to_coords(address: str, api_key: str) -> tuple[float, float]`
  — 실패 시 `geocode.GeocodeError` 예외.

- [ ] **Step 1: 실패하는 테스트 작성**

`crawler/tests/test_geocode.py`:
```python
from unittest.mock import MagicMock, patch

import pytest

from geocode import GeocodeError, address_to_coords


def _mock_response(status_code, json_body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


@patch("geocode.requests.get")
def test_address_to_coords_success(mock_get):
    mock_get.return_value = _mock_response(200, {
        "documents": [{"y": "37.6542", "x": "127.0620"}]
    })
    lat, lng = address_to_coords("서울시 노원구 당고개로 1", api_key="dummy")
    assert lat == 37.6542
    assert lng == 127.0620


@patch("geocode.requests.get")
def test_address_to_coords_sends_auth_header(mock_get):
    mock_get.return_value = _mock_response(200, {"documents": [{"y": "1", "x": "2"}]})
    address_to_coords("아무 주소", api_key="my-key")
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "KakaoAK my-key"


@patch("geocode.requests.get")
def test_address_to_coords_no_results_raises(mock_get):
    mock_get.return_value = _mock_response(200, {"documents": []})
    with pytest.raises(GeocodeError, match="일치하는 주소"):
        address_to_coords("존재하지 않는 주소", api_key="dummy")


@patch("geocode.requests.get")
def test_address_to_coords_http_error_raises(mock_get):
    mock_get.return_value = _mock_response(401, {})
    with pytest.raises(GeocodeError, match="401"):
        address_to_coords("서울시 노원구 당고개로 1", api_key="bad-key")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd crawler && .venv/bin/python -m pytest tests/test_geocode.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'geocode'`)

- [ ] **Step 3: 구현**

`crawler/geocode.py`:
```python
"""주소 문자열 → (위도, 경도). 카카오 로컬 API 사용."""

import requests

KAKAO_GEOCODE_URL = "https://dapi.kakao.com/v2/local/search/address.json"


class GeocodeError(Exception):
    pass


def address_to_coords(address: str, api_key: str) -> tuple[float, float]:
    res = requests.get(
        KAKAO_GEOCODE_URL,
        headers={"Authorization": f"KakaoAK {api_key}"},
        params={"query": address},
        timeout=10,
    )
    if res.status_code != 200:
        raise GeocodeError(f"카카오 API 오류 (status={res.status_code}): {address}")

    documents = res.json().get("documents", [])
    if not documents:
        raise GeocodeError(f"일치하는 주소를 찾지 못했습니다: {address}")

    doc = documents[0]
    return float(doc["y"]), float(doc["x"])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd crawler && .venv/bin/python -m pytest tests/test_geocode.py -v`
Expected: PASS 4건

- [ ] **Step 5: Commit**

```bash
git add crawler/geocode.py crawler/tests/test_geocode.py
git commit -m "feat: geocode.py — 카카오 로컬 API 주소→좌표 변환 (TDD)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: rank_finder.py — XML 파싱 + 순위 판별 (TDD, Task 2 픽스처 사용)

**Files:**
- Create: `crawler/rank_finder.py`
- Test: `crawler/tests/test_rank_finder.py`
- Test fixture: `crawler/tests/fixtures/sample_category_page_source.xml` (Task 2의
  `output/debug/sample_category_page_source.xml`을 복사)

**Interfaces:**
- Consumes: Task 2의 `SPIKE_NOTES.md`에 기록된 실제 노드 속성명(가게명
  텍스트 속성, 광고 배지 판별 속성).
- Produces: `rank_finder.parse_items(xml_sources: list[str]) -> list[dict]`
  — 각 dict `{"name": str, "is_ad": bool}`, 스크롤 간 중복은 `name` 기준으로
  제거하고 최초 등장 순서를 유지. `rank_finder.find_rank(items: list[dict], target_name: str) -> dict`
  — `{"rank": int | None, "total_scanned": int, "ads_above": int}`.

- [ ] **Step 1: 픽스처 복사**

Run: `mkdir -p crawler/tests/fixtures && cp crawler/output/debug/sample_category_page_source.xml crawler/tests/fixtures/`

- [ ] **Step 2: 실패하는 테스트 작성**

**주의:** 아래 테스트의 `assert` 구체값(가게명, 개수)은 Task 2의
`SPIKE_NOTES.md`와 실제 픽스처 내용을 보고 채워 넣는다 — 실제 앱에서 수집한
데이터이므로 이 계획 문서가 값을 미리 알 수 없다. 아래는 채워 넣는 방법의
예시이며, 구현자는 `crawler/tests/fixtures/sample_category_page_source.xml`을
열어 실제 가게명·광고 개수를 확인한 뒤 그 값으로 교체한다.

`crawler/tests/test_rank_finder.py`:
```python
from pathlib import Path

from rank_finder import find_rank, parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "sample_category_page_source.xml"


def test_parse_items_reads_real_fixture_without_error():
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    assert len(items) > 0
    assert all("name" in item and "is_ad" in item for item in items)


def test_parse_items_dedupes_across_scrolls():
    xml = FIXTURE.read_text()
    # 같은 XML을 두 번(스크롤 겹침 흉내) 넣어도 항목 수가 늘어나지 않아야 한다
    items_once = parse_items([xml])
    items_twice = parse_items([xml, xml])
    assert len(items_once) == len(items_twice)


def test_find_rank_for_known_store_in_fixture():
    # SPIKE_NOTES.md를 보고 픽스처 안에 실제로 존재하는 가게명으로 교체할 것
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    known_name = items[2]["name"]  # 픽스처의 3번째 항목으로 임시 검증 (구현자가 실제 이름으로 대체 가능)
    result = find_rank(items, known_name)
    assert result["rank"] == 3


def test_find_rank_not_found_returns_none():
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    result = find_rank(items, "존재하지않는가게이름XYZ123")
    assert result["rank"] is None
    assert result["total_scanned"] == len(items)
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd crawler && .venv/bin/python -m pytest tests/test_rank_finder.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rank_finder'`)

- [ ] **Step 4: 구현**

**주의:** 아래 `TEXT_ATTR`/`AD_BADGE_MARKER` 값은 Task 2의 `SPIKE_NOTES.md`에
기록된 실제 관찰값으로 채운다. 아래 코드의 값은 안드로이드 uiautomator XML의
일반적인 형태(`text` 속성, `resource-id`에 광고 관련 키워드) 기준 기본값이며,
실제 배민 앱 구조와 다르면 구현자가 `SPIKE_NOTES.md` 관찰값으로 교체한다.

`crawler/rank_finder.py`:
```python
"""누적된 page_source XML에서 가게 항목을 순서대로 파싱하고 순위를 찾는다.
Appium/네트워크 의존 없는 순수 함수 — 실제 속성명은 Task 2 스파이크에서
확인한 값을 쓴다 (SPIKE_NOTES.md 참고)."""

import xml.etree.ElementTree as ET

TEXT_ATTR = "text"
AD_BADGE_KEYWORDS = ("ad_badge", "sponsor", "ad_label")  # SPIKE_NOTES.md 관찰값으로 교체


def _is_ad_node(node) -> bool:
    resource_id = node.attrib.get("resource-id", "").lower()
    return any(keyword in resource_id for keyword in AD_BADGE_KEYWORDS)


def parse_items(xml_sources: list[str]) -> list[dict]:
    seen_names = set()
    items = []
    for xml_str in xml_sources:
        root = ET.fromstring(xml_str)
        for node in root.iter():
            text = node.attrib.get(TEXT_ATTR, "").strip()
            if not text or text in seen_names:
                continue
            seen_names.add(text)
            items.append({"name": text, "is_ad": _is_ad_node(node)})
    return items


def find_rank(items: list[dict], target_name: str) -> dict:
    ads_above = 0
    for idx, item in enumerate(items, start=1):
        if item["name"] == target_name:
            return {"rank": idx, "total_scanned": len(items), "ads_above": ads_above}
        if item["is_ad"]:
            ads_above += 1
    return {"rank": None, "total_scanned": len(items), "ads_above": ads_above}
```

- [ ] **Step 5: 테스트 통과 확인 (실제 값으로 조정 후)**

`test_find_rank_for_known_store_in_fixture`의 `known_name`/기대 `rank`를
픽스처 실제 내용에 맞게 조정한 뒤:

Run: `cd crawler && .venv/bin/python -m pytest tests/test_rank_finder.py -v`
Expected: PASS 4건

- [ ] **Step 6: Commit**

```bash
git add crawler/rank_finder.py crawler/tests/test_rank_finder.py crawler/tests/fixtures/
git commit -m "feat: rank_finder.py — 실측 XML 픽스처 기반 순위 파싱 (TDD)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: appium_driver.py + baemin_navigator.py — 실제 앱 조작 통합

**Files:**
- Create: `crawler/appium_driver.py`
- Create: `crawler/baemin_navigator.py`

**Interfaces:**
- Consumes: Task 2의 `SPIKE_NOTES.md` (패키지명, 탭 시퀀스), `rank_finder.parse_items`/`find_rank` (Task 5).
- Produces: `appium_driver.check_server_ready(url: str = "http://localhost:4723") -> None`
  (Appium 서버 미응답 시 `RuntimeError`로 명확한 안내 메시지),
  `appium_driver.start_session(package: str) -> WebDriver`,
  `appium_driver.set_mock_location(lat: float, lng: float) -> None` (adb 래퍼),
  `baemin_navigator.navigate_to_category(driver, category_label: str) -> None`,
  `baemin_navigator.scroll_and_collect(driver, max_scrolls: int = 30) -> list[str]`
  (누적 page_source 문자열 리스트, `rank_finder.parse_items`에 바로 넣을 수 있는 형태).

- [ ] **Step 1: SPIKE_NOTES.md 확인**

`crawler/output/debug/SPIKE_NOTES.md`를 열어 패키지명과 탭 시퀀스를 확인한다.
이 값들을 아래 구현에 그대로 반영한다.

- [ ] **Step 2: appium_driver.py 작성**

`crawler/appium_driver.py`:
```python
"""Appium 세션 시작/종료 + adb 기반 mock GPS 설정."""

import subprocess

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options


def check_server_ready(url: str = "http://localhost:4723") -> None:
    """실행 시작 전 헬스체크 — Appium 서버가 안 떠 있으면 뭘 확인해야 하는지
    바로 알려준다 (스펙의 '에러 처리' 요구사항)."""
    try:
        res = requests.get(f"{url}/status", timeout=3)
        if not res.ok or not res.json().get("value", {}).get("ready", False):
            raise RuntimeError(f"Appium 서버가 준비되지 않았습니다: {url}/status 응답 이상")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Appium 서버에 연결할 수 없습니다 ({url}). "
            "'appium' 명령으로 서버가 떠 있는지, 에뮬레이터가 'adb devices'에 잡히는지 확인하세요."
        ) from e


def start_session(package: str, device_name: str = "emulator-5554"):
    check_server_ready()
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = device_name
    options.app_package = package
    options.no_reset = True
    return webdriver.Remote("http://localhost:4723", options=options)


def set_mock_location(lat: float, lng: float, device_serial: str = "emulator-5554") -> None:
    """adb emu geo fix는 경도, 위도 순서로 받는다."""
    subprocess.run(
        ["adb", "-s", device_serial, "emu", "geo", "fix", str(lng), str(lat)],
        check=True, capture_output=True,
    )


def restart_app(driver, package: str) -> None:
    driver.terminate_app(package)
    driver.activate_app(package)
```

- [ ] **Step 3: baemin_navigator.py 작성 (SPIKE_NOTES.md 탭 시퀀스 반영)**

**주의:** 아래 `navigate_to_category`의 탭 좌표/시퀀스는 반드시
`SPIKE_NOTES.md`에 기록된 실제 관찰값으로 채운다. 아래는 구조 예시다.

`crawler/baemin_navigator.py`:
```python
"""배민 앱 내비게이션 — SPIKE_NOTES.md에서 확인한 실제 탭 시퀀스를 재현한다."""

import time


def _tap(driver, x: int, y: int):
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
    time.sleep(1)


def navigate_to_category(driver, category_label: str) -> None:
    """홈 화면에서 카테고리 탭까지 이동. 좌표는 SPIKE_NOTES.md 관찰값으로 교체."""
    # 예시 — 실제 좌표/탭 순서는 SPIKE_NOTES.md를 보고 채운다
    _tap(driver, 540, 1200)  # 카테고리 메뉴 진입
    # category_label과 일치하는 탭을 accessibility tree에서 찾아 탭하는 방식으로 확장 가능
    elements = driver.find_elements("xpath", f"//*[@text='{category_label}']")
    if not elements:
        raise RuntimeError(f"카테고리 탭을 찾지 못했습니다: {category_label}")
    elements[0].click()
    time.sleep(1)


def scroll_and_collect(driver, max_scrolls: int = 30) -> list[str]:
    """리스트를 아래로 스크롤하며 매번 page_source를 누적 수집한다."""
    sources = [driver.page_source]
    size = driver.get_window_size()
    start_y = int(size["height"] * 0.8)
    end_y = int(size["height"] * 0.2)
    x = int(size["width"] * 0.5)

    for _ in range(max_scrolls):
        driver.execute_script("mobile: swipeGesture", {
            "left": x - 10, "top": end_y, "width": 20, "height": start_y - end_y,
            "direction": "up", "percent": 0.75,
        })
        time.sleep(0.5)
        sources.append(driver.page_source)
    return sources
```

- [ ] **Step 4: 통합 수동 검증 (에뮬레이터 대상 실제 실행)**

Run (에뮬레이터·Appium 서버가 떠 있는 상태에서):
```bash
cd crawler
.venv/bin/python -c "
from appium_driver import start_session, set_mock_location
from baemin_navigator import navigate_to_category, scroll_and_collect
from rank_finder import parse_items, find_rank

driver = start_session('SPIKE_NOTES.md에서_확인한_패키지명')
set_mock_location(37.6542, 127.0620)
navigate_to_category(driver, '치킨')
sources = scroll_and_collect(driver, max_scrolls=10)
items = parse_items(sources)
print(find_rank(items, 'STORE_DISPLAY_NAME값'))
driver.quit()
"
```

Expected: 에러 없이 `{'rank': ..., 'total_scanned': ..., 'ads_above': ...}` 형태 출력.
`rank`가 `None`이면 `max_scrolls`를 늘리거나 카테고리 탭이 제대로 눌렸는지
스크린샷으로 확인한다.

- [ ] **Step 5: Commit**

```bash
git add crawler/appium_driver.py crawler/baemin_navigator.py
git commit -m "feat: appium_driver.py + baemin_navigator.py — 실제 앱 조작 통합

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: run_crawl.py — 메인 파이프라인 조립 + 실제 5지점 실행

**Files:**
- Create: `crawler/run_crawl.py`
- Modify: `crawler/README.md` (환경 구축 섹션 보강)

**Interfaces:**
- Consumes: `config.load_settings`, `geocode.address_to_coords`,
  `geo_sampling.sample_points`, `appium_driver.start_session`/`set_mock_location`/`restart_app`,
  `baemin_navigator.navigate_to_category`/`scroll_and_collect`,
  `rank_finder.parse_items`/`find_rank`.

- [ ] **Step 1: run_crawl.py 작성**

`crawler/run_crawl.py`:
```python
"""5개 지점(가게 + 1/2/3/4km 반경)을 순회하며 배민 카테고리 순위를 실측한다."""

import csv
import datetime
import os
import sys

from appium_driver import restart_app, set_mock_location, start_session
from baemin_navigator import navigate_to_category, scroll_and_collect
from config import RADII_KM, load_settings
from geo_sampling import sample_points
from geocode import GeocodeError, address_to_coords
from rank_finder import find_rank, parse_items

PACKAGE = "여기에_SPIKE_NOTES.md에서_확인한_패키지명"  # Task 2 결과로 채움
OUTPUT_DIR = "output"
CSV_PATH = os.path.join(OUTPUT_DIR, "results.csv")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")


def _classify_rank(result: dict) -> str | int:
    """스펙의 에러 처리 요구사항: 항목을 아예 못 읽었으면(트리 파싱 실패)
    PARSE_ERROR, 항목은 읽었는데 내 가게가 없으면 NOT_FOUND로 구분한다."""
    if result["rank"] is not None:
        return result["rank"]
    if result["total_scanned"] == 0:
        return "PARSE_ERROR"
    return "NOT_FOUND"


def run():
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"설정 오류: {e}")
        sys.exit(1)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    try:
        base_lat, base_lng = address_to_coords(settings.store_address, settings.kakao_api_key)
    except GeocodeError as e:
        print(f"지오코딩 실패, 실행을 중단합니다: {e}")
        sys.exit(1)

    points = [{"radius_km": 0, "bearing_deg": None, "lat": base_lat, "lng": base_lng}]
    points += sample_points(base_lat, base_lng, RADII_KM)

    driver = start_session(PACKAGE)  # 내부적으로 check_server_ready() 호출
    rows = []
    try:
        for point in points:
            set_mock_location(point["lat"], point["lng"])
            restart_app(driver, PACKAGE)
            navigate_to_category(driver, settings.category_label)
            sources = scroll_and_collect(driver, max_scrolls=30)
            items = parse_items(sources)
            result = find_rank(items, settings.store_display_name)
            rank_value = _classify_rank(result)

            timestamp = datetime.datetime.now().isoformat()
            screenshot_name = f"{timestamp.replace(':', '-')}_{point['radius_km']}km.png"
            screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_name)
            driver.save_screenshot(screenshot_path)

            rows.append({
                "timestamp": timestamp,
                "radius_km": point["radius_km"],
                "bearing_deg": point["bearing_deg"],
                "lat": point["lat"],
                "lng": point["lng"],
                "category": settings.category_label,
                "rank": rank_value,
                "total_scanned": result["total_scanned"],
                "ads_above": result["ads_above"],
                "screenshot_path": screenshot_path,
            })
            print(f"[{point['radius_km']}km] rank={rank_value} (scanned {result['total_scanned']}, ads_above {result['ads_above']})")
    finally:
        driver.quit()

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n완료: {CSV_PATH}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: README.md 환경 구축 섹션을 Task 1의 실제 커맨드로 채움**

`crawler/README.md`의 "환경 구축" 부분을 Task 1 Step 2~4에서 실행한 커맨드
그대로 옮겨 적는다 (brew/sdkmanager/avdmanager/appium 설치 커맨드).

- [ ] **Step 3: 실제 실행 (통합 검증)**

에뮬레이터·Appium 서버가 뜬 상태에서:
```bash
cd crawler
.venv/bin/python run_crawl.py
```

Expected: 콘솔에 5개 지점(0/1/2/3/4km)의 rank가 순서대로 출력되고,
`output/results.csv`에 5행, `output/screenshots/`에 스크린샷 5장이 생긴다.
`NOT_FOUND`가 나오면 `max_scrolls`를 늘리거나 `CATEGORY_LABEL`/`STORE_DISPLAY_NAME`
값이 실제 화면 문구와 정확히 일치하는지 확인한다. `PARSE_ERROR`가 나오면
(스캔된 항목이 0건) `navigate_to_category`가 실제로 카테고리 화면에 도달했는지
스크린샷으로 확인하고, `rank_finder.TEXT_ATTR`/`AD_BADGE_KEYWORDS` 값이
`SPIKE_NOTES.md` 관찰값과 일치하는지 다시 확인한다.

- [ ] **Step 4: Commit**

```bash
git add crawler/run_crawl.py crawler/README.md
git commit -m "feat: run_crawl.py — 5개 지점 순회 메인 파이프라인, 실측 완료

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
