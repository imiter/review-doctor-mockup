# 배민 데이터 자동 동기화 백그라운드 스케줄러 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 "데이터 동기화" 버튼을 누르지 않아도, 배민 실계정이 연결된
모든 매장을 매일 KST 04:00에 자동으로 동기화한다.

**Architecture:** Railway 백엔드(FastAPI) `lifespan`에 `asyncio` 무한 루프를
하나 띄운다. 이 루프는 매일 KST 04:00까지 대기한 뒤, 기존 수동 동기화
버튼(`POST /store-connections/baemin/sync-reviews`)이 쓰던 "잡 생성 → 워커
위임" 로직을 그대로 재사용해 배민 실계정이 연결된 모든 매장을 순차
디스패치한다. 새 외부 의존성은 추가하지 않는다.

**Tech Stack:** FastAPI, SQLAlchemy, `asyncio`/`anyio`(이미 FastAPI 의존성으로
설치돼 있음), Next.js(프론트 표시만).

## Global Constraints

- 새 외부 의존성(APScheduler 등)을 추가하지 않는다 — `asyncio.sleep` +
  `anyio.to_thread.run_sync`만 쓴다.
- 스케줄 시각은 KST(Asia/Seoul) 04:00 하드코딩. 사용자가 UI에서 바꾸는
  기능은 이번 범위 밖.
- `CRAWL_WORKER_URL`이 설정된 환경(운영)에서 워커에 위임이 성공한 잡은
  이 프로세스에서 절대 다시 실행하면 안 된다(이중 실행 방지) — 항상
  `store_connections._CRAWL_WORKER_URL` 값을 **모듈 속성으로** 참조한다.
  `from ... import _CRAWL_WORKER_URL`처럼 값만 복사해오면 테스트의
  `monkeypatch.setattr`이 반영되지 않으므로 금지.
- Railway 백엔드는 항상 단일 인스턴스로만 뜬다(`backend/railway.json`에
  `numReplicas` 미지정, 2026-08-20 확인) — 여러 프로세스가 동시에 스케줄을
  도는 상황은 이번 설계에서 고려하지 않는다.
- 모든 새 텍스트/에러 메시지는 기존 코드베이스 관례대로 한국어로 작성한다.
- `backend/app/models.py` 상단 docstring이 명시하듯 CHECK 제약은 DB
  레벨(schema.sql)에만 두고 SQLAlchemy 모델에는 `CheckConstraint`를 추가로
  선언하지 않는다(기존 `status` 컬럼과 동일한 패턴).

---

### Task 1: `review_sync_jobs.triggered_by` 컬럼 추가

**Files:**
- Modify: `schema.sql:346-357` (`review_sync_jobs` 테이블 정의)
- Modify: `backend/app/models.py:194-208` (`ReviewSyncJob` 클래스)
- Test: `backend/tests/test_review_sync_job_model.py` (신규 파일)

**Interfaces:**
- Produces: `ReviewSyncJob.triggered_by: Mapped[str]` (기본값 `"manual"`,
  허용값은 DB CHECK 제약상 `"manual"` 또는 `"scheduled"`이지만 SQLite 테스트
  엔진은 이를 강제하지 않는다 — 기존 `status` 컬럼과 동일). 이후 모든
  Task가 `ReviewSyncJob(...)` 생성 시 이 필드를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_review_sync_job_model.py` 새로 생성:

```python
from datetime import datetime, timezone

from app.models import ReviewSyncJob


def test_review_sync_job_triggered_by_defaults_to_manual(db_session, seeded_user, platforms):
    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()

    row = db_session.query(ReviewSyncJob).filter_by(id=job.id).one()
    assert row.triggered_by == "manual"


def test_review_sync_job_triggered_by_accepts_scheduled(db_session, seeded_user, platforms):
    job = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="pending",
        started_at=datetime.now(timezone.utc), triggered_by="scheduled",
    )
    db_session.add(job)
    db_session.commit()

    row = db_session.query(ReviewSyncJob).filter_by(id=job.id).one()
    assert row.triggered_by == "scheduled"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync_job_model.py -v`
Expected: FAIL — 두 번째 테스트는 `TypeError: 'triggered_by' is an invalid
keyword argument`, 첫 번째 테스트는 `AttributeError:
'ReviewSyncJob' object has no attribute 'triggered_by'` (컬럼이 아직 없음).

- [ ] **Step 3: `schema.sql` 수정**

`schema.sql`의 `review_sync_jobs` 테이블 정의(현재 346-357줄,
`CREATE TABLE review_sync_jobs (`부터 닫는 `);`까지)를 아래로 교체 —
`status` 컬럼 바로 아래에 `triggered_by`를 추가한다:

```sql
CREATE TABLE review_sync_jobs (
    id               BIGSERIAL PRIMARY KEY,
    store_id         BIGINT      NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
    platform_id      INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'success', 'failed')),
    triggered_by     VARCHAR(10) NOT NULL DEFAULT 'manual'
                     CHECK (triggered_by IN ('manual', 'scheduled')),
    reviews_fetched  INT,
    reviews_inserted INT,
    error_message    TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);
```

- [ ] **Step 4: `backend/app/models.py` 수정**

`ReviewSyncJob` 클래스(194-208줄)의 `status` 필드 바로 아래에 추가:

```python
class ReviewSyncJob(Base):
    __tablename__ = "review_sync_jobs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    triggered_by: Mapped[str] = mapped_column(String(10), default="manual")
    reviews_fetched: Mapped[int | None]
    reviews_inserted: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]

    store: Mapped[Store] = relationship()
    platform: Mapped[Platform] = relationship()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync_job_model.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: 전체 백엔드 테스트 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 기존 테스트 전부 그대로 통과(새 컬럼은 기본값이 있어 기존
`ReviewSyncJob(...)` 생성 코드를 깨지 않는다).

- [ ] **Step 7: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_review_sync_job_model.py
git commit -m "feat: review_sync_jobs에 triggered_by(manual/scheduled) 컬럼 추가"
```

> **배포 노트 (이 프로젝트는 Alembic 없이 schema.sql이 DB 정본 — CLAUDE.md
> 참고):** 이 태스크가 머지된 뒤 실제 Railway Postgres에는 아래 SQL을
> 수동으로 한 번 실행해야 한다. 이 SQL 실행은 SDD 서브에이전트의
> 책임이 아니라, 이 플랜을 조율하는 에이전트(오케스트레이터)가 최종
> 배포 시점에 직접 실행한다:
> ```sql
> ALTER TABLE review_sync_jobs
>   ADD COLUMN triggered_by VARCHAR(10) NOT NULL DEFAULT 'manual'
>   CHECK (triggered_by IN ('manual', 'scheduled'));
> ```

---

### Task 2: `_dispatch_sync_job` 공용 헬퍼로 리팩터링

**Files:**
- Modify: `backend/app/routers/store_connections.py:193-252` (`start_review_sync`)
- Test: `backend/tests/test_store_connections.py` (기존 테스트 회귀 확인 + 신규 1개 추가)

**Interfaces:**
- Consumes: Task 1의 `ReviewSyncJob.triggered_by`
- Produces:
  ```python
  def _dispatch_sync_job(
      sid: int, platform: Platform, conn: StorePlatformConnection, db: Session,
      *, triggered_by: str,
      background_tasks: BackgroundTasks | None = None,
  ) -> ReviewSyncJob | None
  ```
  `store_connections` 모듈에 정의되는 module-level 함수. 이미 진행 중인
  잡이 있으면 `None`을 반환(아무 것도 만들지 않음). 아니면 `ReviewSyncJob`을
  만들어 반환한다 — `_CRAWL_WORKER_URL`이 설정돼 있으면 워커에 위임(성공
  시 `status="pending"`, 실패 시 `status="failed"`로 반환), 없고
  `background_tasks`가 주어졌으면 `background_tasks.add_task(run_review_sync_job, job.id)`로
  이 프로세스에서 실행, `background_tasks`도 `None`이면(스케줄러가 워커
  없는 로컬 환경에서 도는 경우) `status="pending"`인 채로 그대로 반환만
  한다 — 호출부가 알아서 실행 책임을 진다. Task 4가 이 함수를
  `store_connections._dispatch_sync_job(...)`로 호출한다(모듈 속성 참조 —
  Global Constraints 참고).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_store_connections.py` 맨 아래에 추가:

```python
def test_sync_reviews_manual_dispatch_records_triggered_by_manual(client, db_session, seeded_user, platforms, auth_headers, monkeypatch):
    from cryptography.fernet import Fernet

    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection
    from app.routers import store_connections as sc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(sc, "run_review_sync_job", lambda job_id: None)

    res = client.post("/store-connections/baemin/sync-reviews", headers=auth_headers)
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    job = db_session.query(ReviewSyncJob).filter_by(id=job_id).one()
    assert job.triggered_by == "manual"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py::test_sync_reviews_manual_dispatch_records_triggered_by_manual -v`
Expected: FAIL — `AttributeError: 'ReviewSyncJob' object has no attribute
'triggered_by'`는 이미 Task 1에서 해결됐으므로, 여기서는 아직 로직이 없어
`triggered_by`가 기본값 `"manual"`이라 사실 이미 통과할 수도 있다. 만약
그렇다면 이 스텝은 "리팩터링 전에도 우연히 통과하지만, Step 3~4에서
`_dispatch_sync_job`을 명시적으로 `triggered_by="manual"`로 호출하는
코드로 바뀌는지가 진짜 목적"이라는 점을 확인만 하고 다음으로 진행한다.

- [ ] **Step 3: `_dispatch_sync_job` 헬퍼 추가 + `start_review_sync` 리팩터링**

`backend/app/routers/store_connections.py`의 193-252줄(`start_review_sync`
전체)을 아래로 교체:

```python
def _dispatch_sync_job(
    sid: int, platform: Platform, conn: StorePlatformConnection, db: Session,
    *, triggered_by: str,
    background_tasks: BackgroundTasks | None = None,
) -> ReviewSyncJob | None:
    """이미 진행 중인 잡이 있으면 None을 반환하고 아무 것도 하지 않는다.
    아니면 잡을 만들고 워커(설정돼 있으면) 또는 이 프로세스의 백그라운드
    작업으로 동기화를 시작시킨 뒤 그 잡을 반환한다.

    CRAWL_WORKER_URL도 없고 background_tasks도 None인 경로(스케줄러가
    워커 없는 로컬 개발 환경에서 도는 경우)에서는 이 함수가 잡을 만들기만
    하고 status="pending"인 채로 반환한다 — 실제로 동기화를 실행하는 건
    호출부 책임이다(app/scheduler.py의 run_scheduled_sync_cycle 참고)."""
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

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py -v`
Expected: PASS 전체 — 기존 `test_sync_reviews_creates_pending_job_and_dispatches_background_task`,
`test_sync_reviews_rejected_while_job_already_in_progress`,
`test_sync_reviews_allowed_after_previous_job_finished`을 포함해 전부
그대로 통과해야 한다(리팩터링이 외부 동작을 바꾸지 않았다는 회귀 확인).

- [ ] **Step 5: 전체 백엔드 테스트 스위트 재확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/store_connections.py backend/tests/test_store_connections.py
git commit -m "refactor: 동기화 잡 생성/디스패치를 _dispatch_sync_job 공용 헬퍼로 분리"
```

---

### Task 3: `GET /store-connections`에 `last_sync` 추가

**Files:**
- Modify: `backend/app/routers/store_connections.py:40-71` (`_row`, `list_connections`)
- Modify: `frontend/src/app/(app)/account/stores/page.tsx` (`Connection` 타입 + 표시)
- Test: `backend/tests/test_store_connections.py`

**Interfaces:**
- Consumes: Task 1의 `ReviewSyncJob.triggered_by`
- Produces: `GET /store-connections` 응답의 각 원소에 필드 추가:
  ```ts
  last_sync: {
    status: "pending" | "running" | "success" | "failed";
    triggered_by: "manual" | "scheduled";
    finished_at: string | null;  // ISO 8601, 아직 안 끝났으면 null
    error_message: string | null;
  } | null;  // 잡이 한 번도 없었으면 null, 배민이 아닌 플랫폼도 항상 null
  ```

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_store_connections.py` 맨 아래에 추가:

```python
def test_list_connections_baemin_last_sync_is_none_when_no_job_exists(client, seeded_user, platforms, auth_headers):
    res = client.get("/store-connections", headers=auth_headers)
    body = res.json()
    assert len(body) == 1
    assert body[0]["last_sync"] is None


def test_list_connections_baemin_last_sync_returns_most_recent_job(client, db_session, seeded_user, platforms, auth_headers):
    from datetime import datetime, timedelta, timezone

    from app.models import ReviewSyncJob

    older = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="success",
        triggered_by="manual", started_at=datetime.now(timezone.utc) - timedelta(days=1),
        finished_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    newer = ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="failed",
        triggered_by="scheduled", started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc), error_message="크롤 워커에 연결할 수 없습니다",
    )
    db_session.add_all([older, newer])
    db_session.commit()

    res = client.get("/store-connections", headers=auth_headers)
    last_sync = res.json()[0]["last_sync"]
    assert last_sync["status"] == "failed"
    assert last_sync["triggered_by"] == "scheduled"
    assert last_sync["error_message"] == "크롤 워커에 연결할 수 없습니다"


def test_list_connections_non_baemin_connection_last_sync_is_always_none(client, seeded_user, platforms, auth_headers):
    res = client.post("/store-connections", json={"platform_id": platforms["yogiyo"].id}, headers=auth_headers)
    assert res.status_code == 201

    body = client.get("/store-connections", headers=auth_headers).json()
    yogiyo_row = next(r for r in body if r["platform_code"] == "yogiyo")
    assert yogiyo_row["last_sync"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py -k last_sync -v`
Expected: FAIL — `KeyError: 'last_sync'` (아직 응답에 이 필드가 없음)

- [ ] **Step 3: `_row`/`list_connections` 수정**

`backend/app/routers/store_connections.py`의 40-71줄을 아래로 교체:

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


def _row(c: StorePlatformConnection, *, last_sync: dict | None = None) -> dict:
    return {
        "id": c.id,
        "platform_id": c.platform_id,
        "platform_code": c.platform.code,
        "platform_name": c.platform.name,
        "brand_color": c.platform.brand_color,
        "platform_store_id": c.platform_store_id,
        "business_number": c.business_number,
        "has_real_credential": c.credential_ciphertext is not None,
        "connected_at": c.connected_at.isoformat(),
        "last_sync": last_sync,
    }


@router.get("/store-connections")
def list_connections(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    store = db.get(Store, sid)
    if store is None or store.user_id != user.id:
        raise HTTPException(404, "매장 없음")

    connections = db.scalars(
        select(StorePlatformConnection)
        .where(StorePlatformConnection.store_id == sid)
        .options(joinedload(StorePlatformConnection.platform))
        .order_by(StorePlatformConnection.connected_at)
    ).all()
    return [
        _row(c, last_sync=_latest_sync_by_store(db, sid, c.platform_id) if c.platform.code == "baemin" else None)
        for c in connections
    ]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_store_connections.py -v`
Expected: PASS 전체(신규 3개 포함)

- [ ] **Step 5: 프론트엔드 타입/표시 추가**

`frontend/src/app/(app)/account/stores/page.tsx`의 `Connection` 타입(9-19줄)을
아래로 교체:

```ts
type Connection = {
  id: number;
  platform_id: number;
  platform_code: string;
  platform_name: string;
  brand_color: string | null;
  platform_store_id: string;
  business_number: string | null;
  has_real_credential: boolean;
  connected_at: string;
  last_sync: {
    status: "pending" | "running" | "success" | "failed";
    triggered_by: "manual" | "scheduled";
    finished_at: string | null;
    error_message: string | null;
  } | null;
};
```

동기화 버튼 블록(188-216줄) 안, `syncStatus` 표시 블록 바로 앞에 아래를
추가(진행 중인 동기화 표시와 겹치지 않도록 `syncingId !== c.id`일 때만):

```tsx
{c.platform_code === "baemin" && c.has_real_credential && (
  <div className="mt-3 border-t border-border-subtle pt-3">
    <button
      onClick={() => startSync(c.id)}
      disabled={syncingId === c.id}
      className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
    >
      {syncingId === c.id ? "동기화 중..." : "데이터 동기화"}
    </button>
    {syncingId !== c.id && c.last_sync && (
      <p className="mt-2 text-xs text-muted">
        마지막 동기화: {c.last_sync.triggered_by === "scheduled" ? "자동" : "수동"} ·{" "}
        {c.last_sync.finished_at ? new Date(c.last_sync.finished_at).toLocaleString("ko-KR") : "진행 중"} ·{" "}
        {c.last_sync.status === "success" && "성공"}
        {c.last_sync.status === "failed" && (
          <span className="text-danger">실패: {c.last_sync.error_message}</span>
        )}
        {(c.last_sync.status === "pending" || c.last_sync.status === "running") && "진행 중"}
      </p>
    )}
    {syncStatus && (
      <>
        <p className="mt-2 text-xs text-muted">
          {syncStatus.status === "success" &&
            `${syncStatus.reviews_fetched}개 중 ${syncStatus.reviews_inserted}개 신규 추가`}
          {syncStatus.status === "failed" && (
            <span className="text-danger">동기화 실패: {syncStatus.error_message}</span>
          )}
          {(syncStatus.status === "pending" || syncStatus.status === "running") && "진행 중..."}
        </p>
        {syncStatus.status !== "failed" && syncStatus.error_message && (
          <p className="mt-1 text-xs text-warning">{syncStatus.error_message}</p>
        )}
      </>
    )}
  </div>
)}
```

이 블록은 기존 188-216줄 전체(버튼 + 기존 `syncStatus` 표시)를 대체한다 —
새로 추가되는 건 `{syncingId !== c.id && c.last_sync && (...)}` 부분뿐이고,
나머지는 기존 코드 그대로다.

- [ ] **Step 6: 로컬에서 화면 확인**

Run: `cd frontend && npm run dev`로 로컬 프론트를 띄우고 `/account/stores`
접속. TypeScript 컴파일 에러가 없는지 확인(`npm run build` 또는 에디터
타입 체크). 실제 배민 연결이 있는 로컬/데모 계정으로 로그인해 "마지막
동기화" 문구가 뜨는지 눈으로 확인한다 — 아직 스케줄러가 없어 잡이 없으면
아무 것도 안 뜨는 게 정상이다(Task 2의 수동 동기화를 한 번 눌러보면
바로 확인 가능).

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/store_connections.py backend/tests/test_store_connections.py "frontend/src/app/(app)/account/stores/page.tsx"
git commit -m "feat: 가게 연결 화면에 마지막 동기화(수동/자동) 정보 표시"
```

---

### Task 4: 스케줄러 코어 (`backend/app/scheduler.py`)

**Files:**
- Create: `backend/app/scheduler.py`
- Test: `backend/tests/test_scheduler.py` (신규 파일)

**Interfaces:**
- Consumes:
  - Task 2의 `store_connections._dispatch_sync_job(sid, platform, conn, db, *, triggered_by, background_tasks=None) -> ReviewSyncJob | None`
  - Task 2 이전부터 있던 `store_connections._CRAWL_WORKER_URL` (module 속성,
    `None` 또는 URL 문자열)
  - `app.review_sync.run_review_sync_job(job_id: int) -> None`
- Produces:
  ```python
  KST: ZoneInfo  # "Asia/Seoul"
  def seconds_until_next_run(now: datetime) -> float
  async def run_scheduled_sync_cycle(db: Session) -> None
  async def run_scheduler_loop() -> None
  ```
  Task 5가 `run_scheduler_loop`를 `main.py`의 `lifespan`에서 기동한다.

- [ ] **Step 1: 실패하는 테스트 작성 — `seconds_until_next_run`**

`backend/tests/test_scheduler.py` 새로 생성:

```python
from datetime import datetime, timezone

from app.scheduler import KST, seconds_until_next_run


def test_seconds_until_next_run_before_4am_same_day():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 3 * 3600


def test_seconds_until_next_run_after_4am_same_day():
    now = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 18 * 3600


def test_seconds_until_next_run_exactly_4am_rolls_to_next_day():
    now = datetime(2026, 8, 20, 4, 0, 0, tzinfo=KST)
    assert seconds_until_next_run(now) == 24 * 3600


def test_seconds_until_next_run_converts_utc_input_to_kst():
    # 2026-08-19 19:00 UTC == 2026-08-20 04:00 KST(UTC+9) — 정확히 목표 시각이라 다음 날로 넘어가야 한다
    now = datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc)
    assert seconds_until_next_run(now) == 24 * 3600
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scheduler'`

- [ ] **Step 3: `backend/app/scheduler.py` 작성**

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
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Platform, StorePlatformConnection
from app.review_sync import run_review_sync_job
from app.routers import store_connections

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
SCHEDULED_HOUR_KST = 4


def seconds_until_next_run(now: datetime) -> float:
    """now(반드시 tz-aware)로부터 다음 KST 04:00까지 남은 초. now가 이미
    오늘 04:00을 지났으면(정확히 04:00인 경우 포함) 내일 04:00까지
    계산한다."""
    now_kst = now.astimezone(KST)
    target = now_kst.replace(hour=SCHEDULED_HOUR_KST, minute=0, second=0, microsecond=0)
    if target <= now_kst:
        target += timedelta(days=1)
    return (target - now_kst).total_seconds()


async def run_scheduled_sync_cycle(db: Session) -> None:
    """배민 실계정이 연결된 모든 매장을 순차적으로 동기화 디스패치한다.
    한 매장 처리 중 예외가 나도 나머지 매장은 계속 진행한다."""
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
            # 개발 환경에서만, 이벤트 루프를 막지 않도록 스레드에서 돌린다.
            if job is not None and job.status == "pending" and not store_connections._CRAWL_WORKER_URL:
                await anyio.to_thread.run_sync(run_review_sync_job, job.id)
        except Exception:
            logger.exception("스케줄된 동기화 실패: store_id=%s", conn.store_id)


async def run_scheduler_loop() -> None:
    while True:
        delay = seconds_until_next_run(datetime.now(KST))
        await asyncio.sleep(delay)
        db = SessionLocal()
        try:
            await run_scheduled_sync_cycle(db)
        except Exception:
            logger.exception("스케줄러 사이클 실행 중 예상치 못한 오류")
        finally:
            db.close()
```

- [ ] **Step 4: `seconds_until_next_run` 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: `run_scheduled_sync_cycle` 실패하는 테스트 작성**

`backend/tests/test_scheduler.py` 파일 맨 위 import 블록을 아래로 교체해
`asyncio`와 `run_scheduled_sync_cycle`을 추가하고(중복 import 방지, 파일
전체에서 import는 맨 위 한 곳에만 모아둔다):

```python
import asyncio
from datetime import datetime, timezone

from app.scheduler import KST, run_scheduled_sync_cycle, seconds_until_next_run
```

그 뒤 기존 4개 테스트 함수 아래에 이어서 추가:

```python


def test_run_scheduled_sync_cycle_skips_connections_without_real_credential(db_session, seeded_user, platforms, monkeypatch):
    import app.scheduler as scheduler_mod
    from app.models import ReviewSyncJob

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert calls == []
    assert db_session.query(ReviewSyncJob).count() == 0


def test_run_scheduled_sync_cycle_dispatches_job_for_real_credential_connection(db_session, seeded_user, platforms, monkeypatch):
    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    job = db_session.query(ReviewSyncJob).filter_by(store_id=seeded_user["store"].id).one()
    assert job.triggered_by == "scheduled"
    assert calls == [job.id]


def test_run_scheduled_sync_cycle_skips_store_with_job_already_in_progress(db_session, seeded_user, platforms, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.add(ReviewSyncJob(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, status="running",
        started_at=datetime.now(timezone.utc), triggered_by="manual",
    ))
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)
    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert calls == []
    assert db_session.query(ReviewSyncJob).filter_by(store_id=seeded_user["store"].id).count() == 1


def test_run_scheduled_sync_cycle_does_not_double_dispatch_when_worker_url_set(db_session, seeded_user, platforms, monkeypatch):
    from unittest.mock import Mock

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.credential_crypto import encrypt_credential
    from app.models import StorePlatformConnection

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_SECRET", "test-secret")
    fake_response = Mock(status_code=200)
    monkeypatch.setattr(scheduler_mod.store_connections.httpx, "post", Mock(return_value=fake_response))

    calls = []
    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", lambda job_id: calls.append(job_id))

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert calls == []  # 워커에 위임됐으니 이 프로세스에서 또 돌리면 이중 실행


def test_run_scheduled_sync_cycle_continues_after_one_store_raises(db_session, seeded_user, platforms, monkeypatch):
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    import app.scheduler as scheduler_mod
    from app.auth import hash_password
    from app.credential_crypto import encrypt_credential
    from app.models import ReviewSyncJob, Store, StorePlatformConnection, User

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn1 = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn1.credential_ciphertext = encrypt_credential("id1", "pw1")

    user2 = User(
        email="demo2@dris.kr", password_hash=hash_password("demo1234!"), nickname="박사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user2)
    db_session.flush()
    store2 = Store(user_id=user2.id, name="족발대장", category="족발", created_at=datetime.now(timezone.utc))
    db_session.add(store2)
    db_session.flush()
    db_session.add(StorePlatformConnection(
        store_id=store2.id, platform_id=platforms["baemin"].id,
        platform_store_id="MK-2", business_number="000-00-00002",
        connected_at=datetime.now(timezone.utc),
        credential_ciphertext=encrypt_credential("id2", "pw2"),
    ))
    db_session.commit()

    monkeypatch.setattr(scheduler_mod.store_connections, "_CRAWL_WORKER_URL", None)

    calls = []

    def _fake_run(job_id):
        job = db_session.get(ReviewSyncJob, job_id)
        if job.store_id == seeded_user["store"].id:
            raise RuntimeError("boom")
        calls.append(job_id)

    monkeypatch.setattr(scheduler_mod, "run_review_sync_job", _fake_run)

    asyncio.run(run_scheduled_sync_cycle(db_session))

    assert len(calls) == 1  # 다른 매장은 정상 처리됨
    assert db_session.query(ReviewSyncJob).count() == 2  # 두 매장 다 잡은 생성됨(실행 성패와 무관)
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: 새로 추가한 5개 중 일부/전부 FAIL — `run_scheduled_sync_cycle`은
Step 3에서 이미 구현했으므로 실제로는 이 시점에 대부분 PASS할 수 있다.
그렇다면 이 스텝은 "Step 3 구현이 이미 이 요구사항들을 충족한다"는 걸
확인하는 것으로 대체한다(구현이 테스트보다 먼저 나온 경우, 순서상
자연스러운 결과 — 아래 Step 7에서 어차피 전체 통과를 재확인한다).

- [ ] **Step 7: 전체 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: PASS (9 passed — seconds_until_next_run 4개 + cycle 5개)

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전체 스위트 통과(회귀 없음)

- [ ] **Step 8: 커밋**

```bash
git add backend/app/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: 배민 자동 동기화 스케줄러 코어(seconds_until_next_run, run_scheduled_sync_cycle) 추가"
```

---

### Task 5: `lifespan` 배선 + CLAUDE.md 갱신

**Files:**
- Modify: `backend/app/main.py` (전체 36줄)
- Test: `backend/tests/test_main.py` (신규 파일)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 4의 `app.scheduler.run_scheduler_loop() -> Coroutine`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_main.py` 새로 생성:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_app_starts_and_serves_health_check_with_scheduler_lifespan():
    """lifespan으로 스케줄러 루프를 띄워도 앱이 정상 기동/응답/종료되는지
    확인한다. run_scheduler_loop는 다음 KST 04:00까지 asyncio.sleep으로
    대기만 하므로(최대 24시간), 이 테스트는 그 sleep이 끝나길 기다리지
    않는다 — with 블록을 빠져나올 때 task.cancel()로 즉시 정리된다."""
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_main.py -v`
Expected: 이 시점엔 `lifespan`이 아직 없어도 `/health`는 정상 응답하므로
PASS할 수 있다 — 즉, 이 테스트는 "지금 당장 실패"가 목적이 아니라
"`lifespan` 배선 후에도 계속 통과해야 하는 안전망"이 목적이다. 다음
Step에서 `main.py`를 바꾼 뒤 다시 실행해 여전히 통과하는지 확인한다.

- [ ] **Step 3: `backend/app/main.py`에 `lifespan` 배선**

`backend/app/main.py` 전체를 아래로 교체:

```python
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ads, auth, billing, dashboard, orders, reply_settings, reviews, sales, store_connections
from app.scheduler import run_scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Delivery Review & Store Insight MVP", lifespan=lifespan)

# FRONTEND_ORIGIN: 배포된 프론트엔드 도메인(예: https://xxx.up.railway.app).
# 로컬 개발은 포트가 매번 달라질 수 있어 정규식으로, 배포본은 고정 도메인 하나만 허용한다.
_frontend_origin = os.getenv("FRONTEND_ORIGIN")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_origins=[_frontend_origin] if _frontend_origin else [],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(reviews.router)
app.include_router(orders.router)
app.include_router(ads.router)
app.include_router(sales.router)
app.include_router(reply_settings.router)
app.include_router(store_connections.router)
app.include_router(billing.router)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_main.py -v`
Expected: PASS — 실제로 스케줄러 루프가 백그라운드에서 기동됐다가
`with` 블록 종료 시 정리된다.

- [ ] **Step 5: 전체 백엔드 테스트 스위트 최종 확인**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전체 통과

- [ ] **Step 6: `CLAUDE.md` 갱신**

`CLAUDE.md`의 "### 배민 데이터 동기화 증분 조회 (예외 허용 아님 — 순수
성능 개선)" 섹션 바로 뒤에 새 섹션을 추가한다(다른 배민 관련 절과 같은
"원래 X였으나 Y로 바꿨다" 서술 방식을 따른다):

```markdown
### 배민 데이터 자동 동기화 스케줄러 (예외 허용 아님 — 순수 편의 기능)
원래 데이터 동기화는 "가게 연결" 화면의 "데이터 동기화" 버튼을 사용자가
직접 눌러야만 실행됐으나(2026-08-19 증분 조회 절 참고), 버튼을 누르지
않아도 매일 자동으로 최신 데이터가 갱신되도록 스케줄러를 추가했다
(2026-08-20). Railway 백엔드 FastAPI `lifespan`(`backend/app/main.py`)이
기동 시 `asyncio` 무한 루프(`backend/app/scheduler.py`의
`run_scheduler_loop`)를 하나 띄우고, 이 루프는 KST 04:00마다 배민 실계정이
연결된(`credential_ciphertext IS NOT NULL`) 모든 매장을 순차적으로
동기화 디스패치한다 — 새 외부 의존성(APScheduler 등) 없이 `asyncio.sleep`
하나로 구현했다. 잡 생성·워커 위임 로직은 수동 버튼과 완전히 같은 함수
(`store_connections._dispatch_sync_job`)를 공유하고,
`review_sync_jobs.triggered_by`(`manual`/`scheduled`) 컬럼으로 어느
경로로 만들어진 잡인지 구분한다. 워커(맥북)가 꺼져있거나 응답이 없으면
그 매장의 잡만 실패로 기록되고 다음 날 04시에 다시 시도된다 — 서버가
죽거나 스케줄러 자체가 멈추지 않는다. Railway 백엔드는 항상 단일
인스턴스로만 뜨므로(`backend/railway.json`에 `numReplicas` 미지정) 여러
프로세스가 같은 스케줄을 중복 실행하는 상황은 고려하지 않았다. "가게
연결" 화면은 배민 카드마다 마지막 동기화가 수동/자동 중 무엇이었는지,
언제, 성공했는지를 `GET /store-connections`의 `last_sync` 필드로
보여준다. 쿠팡이츠/요기요는 여전히 Mock이라 이 스케줄러의 대상이 아니다.
설계 상세는
`docs/superpowers/specs/2026-08-20-baemin-auto-sync-scheduler-design.md`
참고.
```

또한 "### 테이블 용도" 아래 `review_sync_jobs` 설명에 `triggered_by`
컬럼을 언급하도록 한 줄 추가한다 — 현재:
```
- review_sync_jobs: 배민 데이터 동기화 작업 상태(pending/running/success/failed).
  리뷰뿐 아니라 매출/입금/재주문율/우리가게클릭도 같은 작업 안에서 함께
  동기화한다. "가게 연결" 화면의 "데이터 동기화" 버튼 → 백그라운드 작업 →
  폴링에 쓰인다.
```
를 아래로 교체:
```
- review_sync_jobs: 배민 데이터 동기화 작업 상태(pending/running/success/failed).
  리뷰뿐 아니라 매출/입금/재주문율/우리가게클릭도 같은 작업 안에서 함께
  동기화한다. "가게 연결" 화면의 "데이터 동기화" 버튼 → 백그라운드 작업 →
  폴링에 쓰인다. triggered_by(manual/scheduled)로 사용자가 직접 누른
  건지 매일 04시 자동 스케줄러가 만든 건지 구분한다(위 "배민 데이터
  자동 동기화 스케줄러" 절 참고).
```

- [ ] **Step 7: 커밋**

```bash
git add backend/app/main.py backend/tests/test_main.py CLAUDE.md
git commit -m "feat: 배민 데이터 자동 동기화 스케줄러를 앱 lifespan에 배선"
```

---

## 최종 확인 (플랜 조율 에이전트가 직접 수행 — 서브에이전트 위임 대상 아님)

1. `cd backend && .venv/bin/pytest -q` 전체 통과 확인
2. Task 1의 "배포 노트"에 적힌 `ALTER TABLE review_sync_jobs ADD COLUMN
   triggered_by ...`를 실제 Railway Postgres에 실행
3. Railway 백엔드 배포 (`mcp__railway__deploy`)
4. 배포 로그에서 `Application startup complete` 확인 — `lifespan`이
   예외 없이 기동됐다는 뜻
5. "가게 연결" 화면에서 배민 카드에 (아직 자동 동기화가 한 번도 안
   돌았다면) "마지막 동기화" 문구가 없다가, 수동으로 "데이터 동기화"를
   한 번 눌러보면 "마지막 동기화: 수동 · ... · 성공"으로 뜨는지 확인
6. 다음 날 KST 04시 이후, `review_sync_jobs`에
   `triggered_by='scheduled'` 행이 실제로 생겼는지 프로덕션 DB에서 확인
   (즉시 검증 불가 — 다음 날 확인 필요하다는 점을 사용자에게 미리 안내)
