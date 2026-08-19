# 배민 데이터 동기화 백그라운드 스케줄러 설계

## 배경

지금까지 배민 리뷰/매출/입금/재주문율/정산상세/우가클 동기화는 사용자가
"가게 연결" 화면에서 "데이터 동기화" 버튼을 직접 눌러야만 실행됐다
(`POST /store-connections/baemin/sync-reviews`). 증분 조회
(2026-08-19, `docs/superpowers/specs/2026-08-19-baemin-sync-incremental-fetch-design.md`)
와 "바운스" 스크래핑 픽스(2026-08-20, 세션 중 실측 확인 완료)로 동기화
자체는 안정화됐지만, 여전히 사용자가 버튼을 안 누르면 데이터가 며칠이고
갱신되지 않는다. 이 설계는 버튼을 누르지 않아도 매일 자동으로 최신 데이터를
받아오는 스케줄러를 추가한다.

## 목표 / 비목표

**목표**
- 배민 실계정이 연결된 모든 매장을 매일 1회(KST 04:00) 자동으로 동기화한다.
- 기존 수동 동기화 경로와 잡 생성·워커 위임 로직을 공유한다(중복 구현 금지).
- 워커(맥북)가 꺼져있거나 응답이 없으면 조용히 실패 기록만 남기고 다음 날
  재시도한다 — 서버를 죽이거나 무한 재시도하지 않는다.
- "가게 연결" 화면에 마지막 동기화가 수동/자동 중 무엇이었는지, 언제,
  성공했는지를 보여준다.

**비목표**
- 스케줄 주기를 사용자가 UI에서 바꾸는 기능 (하드코딩된 04:00 KST 하나만)
- 여러 매장을 동시에(병렬로) 동기화하는 최적화 — 지금은 실계정 매장이
  데모 1개뿐이라 순차 디스패치로 충분
- Railway 배포가 여러 레플리카로 스케일 아웃되는 경우의 중복 실행 방지 —
  `backend/railway.json`이 `numReplicas`를 지정하지 않아 현재 항상 단일
  인스턴스로 뜨고(2026-08-20 확인), 이 프로젝트 성격상 앞으로도 바뀔
  계획이 없다.

## 아키텍처

Railway 백엔드(`backend/app/main.py`) FastAPI 앱에 `lifespan` 컨텍스트
매니저를 추가해 앱 시작 시 `asyncio.create_task`로 무한 루프
(`backend/app/scheduler.py`의 `run_scheduler_loop`)를 하나 띄운다. 새
외부 의존성(APScheduler 등)은 추가하지 않는다 — `asyncio.sleep` 하나로
충분하다.

루프는:
1. KST 기준 다음 04:00까지 남은 초를 계산해 `asyncio.sleep`으로 대기
2. 깨어나면 `run_scheduled_sync_cycle()` 실행 (아래 "실행 사이클")
3. 1로 돌아가 반복

`run_scheduled_sync_cycle()`은 예외를 전부 잡아 로그만 남기고 삼킨다 —
한 사이클에서 터진 예외가 무한 루프 자체를 죽이면 그 다음 날부터 영원히
자동 동기화가 멈추기 때문에, 이 catch-all은 방어적으로 반드시 필요하다.

## 잡 생성/디스패치 공용화

현재 `backend/app/routers/store_connections.py`의 `start_review_sync`
(라인 193-252)는 다음을 한 함수 안에서 순서대로 한다:
1. 이미 진행 중인 잡이 있으면 409
2. `ReviewSyncJob` 생성 (`status="pending"`)
3. `CRAWL_WORKER_URL`이 있으면 워커의 `/internal/sync-reviews`에 위임,
   없으면 `BackgroundTasks.add_task(run_review_sync_job, job.id)`

이 중 "이미 진행 중인 잡 체크 + 잡 생성 + 위임" 부분을 새 함수로 뽑는다:

```python
# backend/app/routers/store_connections.py

def _dispatch_sync_job(
    sid: int, platform: Platform, conn: StorePlatformConnection, db: Session,
    *, triggered_by: str,
    background_tasks: BackgroundTasks | None = None,
) -> ReviewSyncJob | None:
    """이미 진행 중인 잡이 있으면 None을 반환하고 아무 것도 하지 않는다.
    아니면 잡을 만들고 워커(설정돼 있으면) 또는 이 프로세스의 백그라운드
    작업으로 동기화를 시작시킨 뒤 그 잡을 반환한다.

    background_tasks가 None이면(스케줄러 호출 시 요청 컨텍스트가 없음)
    CRAWL_WORKER_URL이 없는 경로에서 anyio.to_thread.run_sync로 스레드에
    올려 이벤트 루프를 막지 않는다.
    """
    existing_job = db.scalar(
        select(ReviewSyncJob).where(
            ReviewSyncJob.store_id == sid,
            ReviewSyncJob.platform_id == platform.id,
            ReviewSyncJob.status.in_(("pending", "running")),
        )
    )
    if existing_job is not None:
        return None

    job = ReviewSyncJob(
        store_id=sid, platform_id=platform.id, status="pending",
        started_at=datetime.now(timezone.utc), triggered_by=triggered_by,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if _CRAWL_WORKER_URL:
        try:
            resp = httpx.post(
                f"{_CRAWL_WORKER_URL}/internal/sync-reviews",
                params={"job_id": job.id},
                headers={"X-Worker-Secret": _CRAWL_WORKER_SECRET},
                timeout=15,
            )
        except httpx.RequestError as e:
            job.status = "failed"
            job.error_message = f"크롤 워커에 연결할 수 없습니다: {e}"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return job
        if resp.status_code != 200:
            job.status = "failed"
            job.error_message = f"크롤 워커 실행 실패: {resp.text[:500]}"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
        return job

    if background_tasks is not None:
        background_tasks.add_task(run_review_sync_job, job.id)
    return job
```

> `CRAWL_WORKER_URL`도 없고 `background_tasks`도 `None`인 경로(로컬 개발
> 환경에서 스케줄러가 직접 도는 경우)는 이 함수가 아무 것도 더 하지 않고
> `status="pending"`인 `job`을 그대로 반환한다. `_dispatch_sync_job`은
> 동기 함수라 여기서 스레드를 띄우면 호출부가 그 결과를 기다릴 방법이
> 없기 때문에, 실제 동기화 실행은 항상 호출부 책임이다 — FastAPI 요청
> 경로는 `background_tasks`로, 스케줄러는 아래 `run_scheduled_sync_cycle`이
> `anyio.to_thread.run_sync(run_review_sync_job, job.id)`를 **await**하는
> 방식으로 각자 처리한다.

`start_review_sync`는 이 헬퍼를 호출하도록 바꾸고, 헬퍼가 `None`을
반환하면(이미 진행 중) 기존처럼 409를 던진다:

```python
@router.post("/store-connections/baemin/sync-reviews", status_code=202)
def start_review_sync(
    background_tasks: BackgroundTasks, store_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
    conn = db.scalar(
        select(StorePlatformConnection).where(
            StorePlatformConnection.store_id == sid, StorePlatformConnection.platform_id == platform.id
        )
    )
    if conn is None or conn.credential_ciphertext is None:
        raise HTTPException(400, "먼저 배민 로그인이 필요합니다")

    job = _dispatch_sync_job(sid, platform, conn, db, triggered_by="manual", background_tasks=background_tasks)
    if job is None:
        raise HTTPException(409, "이미 진행 중인 동기화가 있습니다")
    return {"job_id": job.id}
```

## 스케줄러 실행 사이클

새 파일 `backend/app/scheduler.py`:

```python
"""배민 데이터 자동 동기화 스케줄러. 사용자가 "데이터 동기화" 버튼을 안
눌러도 매일 KST 04:00에 배민 실계정이 연결된 모든 매장을 자동으로
동기화한다. 새 외부 의존성 없이 asyncio만으로 구현한다 — 이 프로젝트는
Railway에서 항상 단일 인스턴스로만 돌기 때문에(backend/railway.json에
numReplicas 미지정) 여러 프로세스가 동시에 스케줄을 도는 상황을 걱정할
필요가 없다.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anyio
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Platform, StorePlatformConnection
from app.review_sync import run_review_sync_job
from app.routers import store_connections

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
SCHEDULED_HOUR_KST = 4


def seconds_until_next_run(now: datetime) -> float:
    """now(반드시 tz-aware)로부터 다음 KST 04:00까지 남은 초. now가 이미
    오늘 04:00을 지났으면 내일 04:00까지 계산한다."""
    now_kst = now.astimezone(KST)
    target = now_kst.replace(hour=SCHEDULED_HOUR_KST, minute=0, second=0, microsecond=0)
    if target <= now_kst:
        target += timedelta(days=1)
    return (target - now_kst).total_seconds()


async def run_scheduled_sync_cycle() -> None:
    """배민 실계정이 연결된 모든 매장을 순차적으로 동기화 디스패치한다.
    한 매장 처리 중 예외가 나도 나머지 매장은 계속 진행한다."""
    db = SessionLocal()
    try:
        platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
        if platform is None:
            return
        connections = db.scalars(
            select(StorePlatformConnection).where(
                StorePlatformConnection.platform_id == platform.id,
                StorePlatformConnection.credential_ciphertext.isnot(None),
            )
        ).all()
        for conn in connections:
            try:
                job = store_connections._dispatch_sync_job(
                    conn.store_id, platform, conn, db, triggered_by="scheduled", background_tasks=None,
                )
                # CRAWL_WORKER_URL이 설정된 환경(운영)에서는 _dispatch_sync_job이
                # 이미 워커에 위임을 마쳤다 — 여기서 또 로컬로 돌리면 같은
                # 동기화가 두 번(워커+이 프로세스) 실행된다. 워커가 없는 로컬
                # 개발 환경에서만, 이벤트 루프를 막지 않도록 스레드에서
                # 돌린다. 모듈 속성으로 참조해야 테스트의
                # monkeypatch.setattr(store_connections, "_CRAWL_WORKER_URL", ...)가
                # 그대로 먹힌다 — `from ... import _CRAWL_WORKER_URL`로 값만
                # 복사해오면 몽키패치가 반영되지 않는다.
                if job is not None and job.status == "pending" and not store_connections._CRAWL_WORKER_URL:
                    await anyio.to_thread.run_sync(run_review_sync_job, job.id)
            except Exception:
                logger.exception("스케줄된 동기화 실패: store_id=%s", conn.store_id)
    finally:
        db.close()


async def run_scheduler_loop() -> None:
    while True:
        delay = seconds_until_next_run(datetime.now(KST))
        await asyncio.sleep(delay)
        try:
            await run_scheduled_sync_cycle()
        except Exception:
            logger.exception("스케줄러 사이클 실행 중 예상치 못한 오류")
```

`backend/app/main.py`는 `lifespan`으로 이 루프를 기동한다:

```python
import asyncio
from contextlib import asynccontextmanager

from app.scheduler import run_scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Delivery Review & Store Insight MVP", lifespan=lifespan)
```

`anyio`는 FastAPI/Starlette가 이미 의존성으로 깔아주므로 `requirements.txt`
변경이 필요 없다.

## DB 변경

`review_sync_jobs`에 컬럼 하나 추가 (schema.sql):

```sql
    triggered_by     VARCHAR(10) NOT NULL DEFAULT 'manual'
                     CHECK (triggered_by IN ('manual', 'scheduled')),
```

`status` 컬럼 바로 아래 줄에 추가한다. `backend/app/models.py`의
`ReviewSyncJob`에도 동일하게 반영:

```python
    triggered_by: Mapped[str] = mapped_column(String(10), default="manual")
```

기존 행은 전부 수동으로 만들어진 것이므로 `DEFAULT 'manual'`이 소급 적용
되는 게 정확하다 — 백필 스크립트 불필요.

## API 변경

`GET /store-connections`의 `_row()`가 배민 연결 건에 대해 최신 잡 정보를
같이 내려준다. `list_connections`에서 연결 목록을 가져온 뒤, 배민
`platform_id`에 대해서만 매장별 최신 `ReviewSyncJob`을 조회해 딕셔너리로
붙인다:

```python
def _latest_sync_by_store(db: Session, store_id: int, platform_id: int) -> dict | None:
    job = db.scalar(
        select(ReviewSyncJob)
        .where(ReviewSyncJob.store_id == store_id, ReviewSyncJob.platform_id == platform_id)
        .order_by(ReviewSyncJob.started_at.desc())
        .limit(1)
    )
    if job is None:
        return None
    return {
        "status": job.status,
        "triggered_by": job.triggered_by,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_message": job.error_message,
    }
```

`_row()`는 `last_sync` 필드를 추가로 받아 그대로 실어 보낸다. 연결이
여러 개면 `list_connections`가 배민 건에 대해서만 이 조회를 한 번씩
호출한다(매장당 배민 연결은 최대 1개이므로 N+1이 실질적으로 문제되지
않는 규모).

## 프론트엔드

`frontend/src/app/(app)/account/stores/page.tsx`:

- `Connection` 타입에 필드 추가:
  ```ts
  last_sync: {
    status: "pending" | "running" | "success" | "failed";
    triggered_by: "manual" | "scheduled";
    finished_at: string | null;
    error_message: string | null;
  } | null;
  ```
- 배민 카드 안, `syncingId === c.id`가 아닐 때(진행 중 표시와 겹치지
  않게) `c.last_sync`가 있으면 아래에 한 줄 표시:
  ```tsx
  {syncingId !== c.id && c.last_sync && (
    <p className="mt-2 text-xs text-muted">
      마지막 동기화: {c.last_sync.triggered_by === "scheduled" ? "자동" : "수동"} ·{" "}
      {c.last_sync.finished_at ? new Date(c.last_sync.finished_at).toLocaleString("ko-KR") : "진행 중"} ·{" "}
      {c.last_sync.status === "success" && "성공"}
      {c.last_sync.status === "failed" && <span className="text-danger">실패: {c.last_sync.error_message}</span>}
      {(c.last_sync.status === "pending" || c.last_sync.status === "running") && "진행 중"}
    </p>
  )}
  ```
- 별도 API 호출 없음 — `load()`가 이미 받아오는 `/store-connections`
  응답에 얹혀온다.

## 테스트 계획

1. `backend/tests/test_scheduler.py` (신규):
   - `seconds_until_next_run`: 자정 전/후, 정확히 04:00일 때, 04:00을
     막 지났을 때 등 경계 케이스 순수 함수 테스트.
   - `run_scheduled_sync_cycle`: `credential_ciphertext`가 있는 연결만
     대상이 되는지, 이미 진행 중인 잡이 있으면 스킵하는지, 한 매장에서
     예외가 나도 다음 매장을 계속 처리하는지 — `_dispatch_sync_job`과
     `run_review_sync_job`을 monkeypatch해서 실제 Playwright/HTTP 없이
     검증.
2. `backend/tests/test_store_connections.py` (확장):
   - `start_review_sync`가 `_dispatch_sync_job`을 통해 여전히 기존과
     동일하게 동작하는지(리팩터링 후 회귀 확인) — 기존 테스트들이 이미
     이 경로를 커버하므로 대부분 그대로 통과해야 하고, `triggered_by`
     값이 `"manual"`로 저장되는지 확인하는 assertion만 추가.
   - `GET /store-connections`가 `last_sync`를 정확히 내려주는지(잡 없음
     → `None`, 여러 잡 중 최신 것만 반환) 신규 테스트.
3. 프론트엔드는 이 프로젝트 관례상 백엔드 pytest만 자동화하고, 화면
   확인은 로컬에서 수동으로 한다(기존 다른 화면들과 동일한 패턴).
