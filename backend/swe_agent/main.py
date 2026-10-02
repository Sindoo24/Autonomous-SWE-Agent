"""FastAPI application factory.

The API never runs the agent itself: it records tasks and runs in PostgreSQL and enqueues jobs
for the worker (`swe-agent worker`). Layering: `api/routes` -> `services` -> `db/store`, with
the agent runtime (`agents/runner`) read for live checkpoint state.

Run with `swe-agent api`, or `uvicorn --factory swe_agent.main:create_app` (settings from the
environment).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from swe_agent.agents.runner import setup_checkpoints
from swe_agent.api import router
from swe_agent.config import Settings
from swe_agent.db.engine import DatabaseNotConfiguredError, make_async_engine, session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.database_url:
        raise DatabaseNotConfiguredError()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_async_engine(settings.database_url or "")
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = session_factory(engine)
        await setup_checkpoints(settings)
        yield
        await engine.dispose()

    app = FastAPI(
        title="Autonomous SWE Agent", version="0.8.0", lifespan=lifespan,
        description="Submit a repository + issue; the agent returns a verified patch.",
    )  # fmt: skip
    router.install(app)
    return app
