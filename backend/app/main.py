from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ads, auth, dashboard, orders, reply_settings, reviews, sales, store_connections

app = FastAPI(title="Delivery Review & Store Insight MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
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
