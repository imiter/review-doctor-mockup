#!/bin/bash
# 반경별 실측 순위 "워커" 4종 세트를 순서대로 띄운다: 에뮬레이터 → Appium →
# 워커 백엔드(Railway 프로덕션 DB에 연결) → ngrok 고정 터널.
# 이미 떠 있는 건 건너뛴다(멱등) — launchd가 로그인 때마다 재실행해도 안전하다.
#
# 비밀값(DB 접속 문자열, 워커 공유 비밀)은 이 파일에 두지 않고 .env.worker에서
# 읽는다(.env.worker는 gitignore됨 — 저장소에 커밋되지 않는다).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

set -a
source .env.worker
set +a

mkdir -p logs

export ANDROID_HOME="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"
export PATH="$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:/opt/homebrew/bin"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# 1. 안드로이드 에뮬레이터
if ! adb devices | grep -q "device$"; then
  log "에뮬레이터 시작..."
  nohup emulator -avd "$AVD_NAME" >logs/emulator.log 2>&1 &
  disown
  for _ in $(seq 1 60); do
    if adb devices | grep -q "device$" && [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
      break
    fi
    sleep 5
  done
  log "에뮬레이터 부팅 완료"
else
  log "에뮬레이터 이미 실행 중 — 건너뜀"
fi

# 2. Appium 서버
if ! curl -s -o /dev/null http://localhost:4723/status; then
  log "Appium 서버 시작..."
  nohup appium >logs/appium.log 2>&1 &
  disown
  for _ in $(seq 1 30); do
    curl -s -o /dev/null http://localhost:4723/status && break
    sleep 2
  done
  log "Appium 서버 준비됨"
else
  log "Appium 서버 이미 실행 중 — 건너뜀"
fi

# 3. 워커 백엔드 (Railway 프로덕션 Postgres에 연결)
if ! curl -s -o /dev/null "http://localhost:${WORKER_PORT}/health"; then
  log "워커 백엔드 시작..."
  (
    cd "$SCRIPT_DIR/../backend"
    nohup .venv/bin/uvicorn app.main:app --port "$WORKER_PORT" >"$SCRIPT_DIR/logs/worker-backend.log" 2>&1 &
    disown
  )
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://localhost:${WORKER_PORT}/health" && break
    sleep 2
  done
  log "워커 백엔드 준비됨"
else
  log "워커 백엔드 이미 실행 중 — 건너뜀"
fi

# 4. ngrok 고정 터널
if ! pgrep -f "ngrok http" >/dev/null; then
  log "ngrok 터널 시작..."
  nohup ngrok http --url="$NGROK_URL" "$WORKER_PORT" >logs/ngrok.log 2>&1 &
  disown
  sleep 3
  log "ngrok 터널 시작됨 ($NGROK_URL)"
else
  log "ngrok 이미 실행 중 — 건너뜀"
fi

log "모든 워커 서비스 준비 완료"
