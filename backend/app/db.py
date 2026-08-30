import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# DB 정본은 schema.sql / seed.sql (저장소 루트). 모델은 스키마에 1:1로 맞춘다.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/delivery_insight"
)


class Base(DeclarativeBase):
    pass


# pool_pre_ping: 커넥션 풀에서 꺼내 쓰기 전에 가벼운 SELECT 1로 살아있는지
# 확인하고, 죽어있으면(클라우드 Postgres가 오래 idle인 커넥션을 서버 쪽에서
# 먼저 끊는 경우가 흔함) 자동으로 새로 연결한다 — 이게 없으면 오래 idle이었던
# 커넥션을 그대로 재사용하려다 "SSL SYSCALL error: Operation timed out"로
# 죽는다(2026-08-30 실측: 크롤 워커가 이 오류로 동기화 잡을 시작하자마자
# 죽여서 잡이 pending에 영원히 갇힘). pool_recycle은 보험으로 30분 이상 묵은
# 커넥션은 pre_ping 여부와 무관하게 무조건 새로 만든다.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
