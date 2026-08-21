import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ads, auth, billing, dashboard, orders, reply_onboarding, reply_settings, reviews, sales, store_connections
from app.scheduler import run_scheduler_loop

# 스케줄러는 기본 OFF다 — Railway 백엔드 프로세스에서만 명시적으로 켠다.
# crawler/start_worker_services.sh가 띄우는 크롤 워커(맥북)도 같은
# app.main:app을 같은 운영 Postgres에 대고 그대로 실행하지만
# CRAWL_WORKER_URL은 설정하지 않는다(그 프로세스 자신이 워커라 더 위임할
# 곳이 없어서) — 만약 스케줄러가 항상 켜져 있었다면 Railway와 워커 두
# 프로세스가 동시에 같은 KST 04:00 순간에 같은 매장·같은 배민 계정을 향해
# 각자 독립적으로 동기화를 실행하는 이중 실행 사고가 난다(이 기능 전체가
# 막으려던 바로 그 버그가 다른 경로로 재발). ENABLE_SYNC_SCHEDULER=true를
# Railway 백엔드 서비스에만 설정하고 crawler/.env.worker에는 절대 설정하지
# 않는다.


def _env_flag_enabled(value: str | None) -> bool:
    return (value or "").lower() in ("1", "true")


_SCHEDULER_ENABLED = _env_flag_enabled(os.getenv("ENABLE_SYNC_SCHEDULER"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_scheduler_loop()) if _SCHEDULER_ENABLED else None
    yield
    if task is not None:
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
app.include_router(reply_onboarding.router)
app.include_router(store_connections.router)
app.include_router(billing.router)
