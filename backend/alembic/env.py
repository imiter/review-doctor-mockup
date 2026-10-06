"""Alembic 실행 환경.

DB URL은 alembic.ini에 적지 않고 이 파일에서 런타임에 읽는다 — 운영(Railway)
/크롤 워커/로컬이 전부 `DATABASE_URL` 환경변수 하나로 접속 대상을 정하는
기존 관례(`app/db.py`)를 마이그레이션도 똑같이 따르게 하려는 것이다. ini에
URL을 하드코딩하면 "코드가 보는 DB"와 "마이그레이션이 보는 DB"가 조용히
갈라질 수 있다(이 프로젝트에서 환경변수 누락으로 두 번 사고가 났던 것과
같은 종류의 실수다 — CLAUDE.md "Alembic 도입" 절 참고).

`target_metadata`는 `app.db.Base.metadata`다. `app.models`를 import해야
모델 클래스가 실제로 Base에 등록되므로 반드시 함께 import한다 —
autogenerate가 테이블을 못 보고 "전부 삭제"하는 마이그레이션을 만드는 사고를
막는 유일한 장치다.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401 — Base에 모델을 등록하는 import (지우면 안 됨)
from app.db import DATABASE_URL, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
