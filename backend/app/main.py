from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.ads import router as ads_router
from app.routers.reviews import router as reviews_router
from app.routers.settlements import router as settlements_router

app = FastAPI(title="Review Doctor MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(ads_router)
app.include_router(reviews_router)
app.include_router(settlements_router)
