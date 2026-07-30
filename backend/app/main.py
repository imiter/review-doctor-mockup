import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ads, auth, dashboard, orders, reply_settings, reviews, sales, store_connections

app = FastAPI(title="Delivery Review & Store Insight MVP")

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
