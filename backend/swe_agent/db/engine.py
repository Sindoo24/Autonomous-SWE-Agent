"""Engines, sessions and migrations. One driver (psycopg 3) for sync, async and LangGraph."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class DatabaseNotConfiguredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "SWE_DATABASE_URL is not set (e.g. postgresql+psycopg://swe:swe@localhost:5432/"
            "swe_agent). The API, worker and experiment runner need PostgreSQL."
        )


def sqlalchemy_url(url: str) -> str:
    """Normalise to the psycopg 3 driver: postgres://, postgresql:// -> postgresql+psycopg://"""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def libpq_url(url: str) -> str:
    """Plain libpq URL for psycopg / LangGraph's Postgres saver."""
    return sqlalchemy_url(url).replace("postgresql+psycopg://", "postgresql://", 1)


def is_postgres(url: str | None) -> bool:
    return bool(url) and sqlalchemy_url(url or "").startswith("postgresql+psycopg://")


def make_async_engine(url: str) -> AsyncEngine:
    return create_async_engine(sqlalchemy_url(url), pool_pre_ping=True, pool_size=5)


def make_sync_engine(url: str) -> Engine:
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True, pool_size=2)


def session_factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False)


def upgrade(url: str, revision: str = "head") -> None:
    """Apply Alembic migrations (the same thing `swe-agent db upgrade` does)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    command.upgrade(cfg, revision)
