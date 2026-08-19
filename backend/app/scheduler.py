"""배민 데이터 자동 동기화 스케줄러. 사용자가 "데이터 동기화" 버튼을 안
눌러도 매일 KST 04:00에 배민 실계정이 연결된 모든 매장을 자동으로
동기화한다. 새 외부 의존성 없이 asyncio만으로 구현한다 — 이 프로젝트는
Railway에서 항상 단일 인스턴스로만 돌기 때문에(backend/railway.json에
numReplicas 미지정) 여러 프로세스가 동시에 스케줄을 도는 상황을 걱정할
필요가 없다.
"""

import asyncio
import functools
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
            # _dispatch_sync_job은 동기 함수이고 CRAWL_WORKER_URL이 설정된
            # 환경에서는 그 안에서 blocking httpx.post(timeout=15)를 직접
            # 호출한다 — 이벤트 루프를 막지 않도록 스레드에서 돌린다(디스패치
            # 자체는 스토어마다 순차적으로 await되므로 같은 db 세션을 여러
            # 스레드가 동시에 건드릴 일은 없다).
            job = await anyio.to_thread.run_sync(
                functools.partial(
                    store_connections._dispatch_sync_job,
                    conn.store_id, platform, conn, db,
                    triggered_by="scheduled", background_tasks=None,
                )
            )
            # CRAWL_WORKER_URL이 설정된 환경(운영)에서는 _dispatch_sync_job이
            # 이미 워커에 위임을 마쳤다 — 여기서 또 로컬로 돌리면 같은
            # 동기화가 두 번(워커+이 프로세스) 실행된다. 워커가 없는 로컬
            # 개발 환경에서만, 이벤트 루프를 막지 않도록 스레드에서 돌린다.
            if job is not None and job.status == "pending" and not store_connections._CRAWL_WORKER_URL:
                await anyio.to_thread.run_sync(run_review_sync_job, job.id)
        except Exception:
            logger.exception("스케줄된 동기화 실패: store_id=%s", conn.store_id)
            # db.commit()이 여기서 실패했을 수 있다(제약 위반, 커넥션 끊김 등) —
            # 롤백하지 않으면 세션이 PendingRollbackError 상태로 남아 이후
            # 매장 전부가 첫 select()에서부터 연쇄로 실패한다.
            db.rollback()


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
