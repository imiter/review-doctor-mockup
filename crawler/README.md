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

### 1. Android 커맨드라인 툴 + 에뮬레이터 설치

```bash
brew install openjdk@17
brew install --cask android-commandlinetools
```

`~/.zshrc` 또는 현재 셸에 추가:

```bash
export ANDROID_HOME="$(brew --prefix)/share/android-commandlinetools"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"
```

라이선스 동의 + 필수 패키지 설치:

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

### 2. 에뮬레이터 부팅 확인 (창이 보이는 상태로 — 사람이 조작해야 함)

```bash
emulator -avd baemin_test &
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
adb devices
```

`-no-window`는 사용하지 않는다 — 에뮬레이터 창이 화면에 떠 있어야 한다.

### 3. Appium 서버 + uiautomator2 드라이버 설치

```bash
npm install -g appium
appium driver install uiautomator2
appium &
sleep 3
curl -s http://localhost:4723/status
```

### 4. 파이썬 가상환경 + 의존성 설치

```bash
cd crawler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_crawl.py
```

결과는 `output/results.csv`와 `output/screenshots/`에 저장된다.
