# 배민 카테고리 순위 실측 크롤러

메인 프로젝트(review-doctor)와 분리된 독립 도구. 내 가게 기준 1/2/3/4km
반경 지점에서 배민 카테고리 순위를 실제로 측정한다. 1회성 테스트 목적이며
상시 자동 수집이 아니다.

## 준비물

1. `.env.example`을 `.env`로 복사하고 값을 채운다 (카카오 REST API 키는
   https://developers.kakao.com 에서 애플리케이션 등록 후 발급받는다).
   카카오 개발자 콘솔에 IP 제한이 걸려 있으면 이 크롤러를 실행하는 머신의
   외부 IP를 허용 목록에 추가해야 한다(막혀 있으면 지오코딩 호출이
   401 `AccessDeniedError`로 실패한다).
   **`CATEGORY_LABEL`과 `STORE_DISPLAY_NAME`은 배민 앱 화면에 실제로 보이는
   문구와 글자 하나까지 정확히 일치해야 한다** — 스크립트가 추측하지 않으므로
   앱을 직접 열어 확인한 값을 그대로 적는다. 카테고리는 앱의 상단 탭 바
   또는 "메뉴 전체보기"로 펼친 전체 목록에 있는 정확한 이름이어야 한다
   (예: "구이"는 존재하지 않고 "고기"가 실제 카테고리명이다).
2. Android SDK/에뮬레이터/Appium 환경은 아래 "환경 구축" 참고.
3. 에뮬레이터에 배달의민족 앱을 Play스토어에서 직접 설치한다 (수동 — 아래
   "Play스토어 설치" 섹션 참고).

## 환경 구축

### 1. Java + Android 커맨드라인 툴 설치

```bash
brew install openjdk@17
brew install --cask android-commandlinetools
```

`~/.zshrc` 또는 현재 셸에 추가:

```bash
export JAVA_HOME="$(brew --prefix openjdk@17)"
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="$(brew --prefix)/share/android-commandlinetools"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"
```

라이선스 동의 + 필수 패키지 설치:

```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "platforms;android-34"
```

### 2. 시스템 이미지 + AVD 생성 (Mac 아키텍처별)

Apple Silicon (`arm64`) 또는 Intel 아키텍처에 맞춰 설치:

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

### 3. 에뮬레이터 부팅 확인 (창이 보이는 상태로 — 사람이 조작해야 함)

**중요**: `-no-window`는 사용하지 않는다 — 에뮬레이터 창이 화면에 떠 있어야 한다.

```bash
emulator -avd baemin_test &
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
adb devices
```

### 4. 에뮬레이터 시스템 로케일을 한국어로 설정 (필수)

배민 앱 카테고리/가게명이 한국어로 나오려면 AVD의 시스템 로케일을 `ko-KR`로
설정해야 한다. `google_apis_playstore` 이미지는 non-rooted이므로 라이브 브로드캐스트
(`android.intent.action.LOCALE_CHANGED`)가 거부된다. 대신 다음 명령으로 재부팅:

```bash
adb shell settings put system system_locales ko-KR
adb reboot
adb wait-for-device
```

### 5. Play스토어에서 배달의민족 앱 설치 (사람이 직접 조작 — 자동화하지 않음)

에뮬레이터 창에서 직접:

1. 설정 → 계정 → Google 로그인 (본인 Google 계정으로).
2. Play 스토어 앱을 열어 "배달의민족" 검색 → 설치.
3. 앱을 한 번 실행해서 위치 권한 요청이 뜨면 "앱 사용 중에만 허용" 선택.
4. 위치 관련 온보딩 화면이 있으면 넘겨서 가게 목록이 보이는 홈 화면까지
   도달한다. 계정 로그인은 하지 않는다 — 배민은 로그인 없이 가게 목록을
   볼 수 있고, 이 도구는 로그인/계정을 다루지 않는다.

설치된 앱의 실제 패키지명은 `com.sampleapp`이다(배민 자체 브랜딩과 무관한
임의 패키지명 — `adb shell pm list packages | grep -i baemin`으로는 안
잡히니 `adb shell pm list packages | grep sampleapp`으로 확인할 것).
`run_crawl.py`의 `PACKAGE` 상수에 이미 반영돼 있다.

### 6. Appium 서버 + uiautomator2 드라이버 설치

```bash
npm install -g appium
appium driver install uiautomator2
appium &
sleep 3
curl -s http://localhost:4723/status
```

### 7. 파이썬 가상환경 + 의존성 설치

```bash
cd crawler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 테스트

순수 함수 모듈(`geo_sampling.py`, `geocode.py`, `rank_finder.py`)의 단위
테스트는 실기기/에뮬레이터 없이 실행된다. 반드시 `crawler/` 디렉터리에서
실행한다(모듈 임포트가 현재 디렉터리 기준):

```bash
cd crawler
.venv/bin/python -m pytest -v
```

## 실행

에뮬레이터가 켜져 있고 배민 앱이 설치된 상태에서, 반드시 `crawler/`
디렉터리에서 실행한다(`.env` 로드와 `output/` 경로가 현재 디렉터리 기준
상대경로다):

```bash
cd crawler
.venv/bin/python run_crawl.py
```

결과는 `output/results.csv`와 `output/screenshots/`에 저장된다. 지점별로
가게를 찾은 시점(또는 최대 스크롤 도달 시점)의 화면을 스크린샷으로 남기며,
`PARSE_ERROR`가 발생하면 원인 분석용 원본 화면 데이터가 `output/debug/`에
함께 저장된다. 한 지점에서 앱 조작이 실패해도(`NAV_ERROR`) 전체 실행은
중단되지 않고 나머지 지점을 계속 진행한다.
