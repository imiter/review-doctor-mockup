from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.reviews import router as reviews_router

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


app.include_router(reviews_router)
