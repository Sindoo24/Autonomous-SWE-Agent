"""Assembles the versioned API and maps service errors to HTTP status codes."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from swe_agent.api.routes import experiments, health, runs, tasks
from swe_agent.core.errors import ConflictError, InvalidRequestError, NotFoundError

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)
api_router.include_router(tasks.router)
api_router.include_router(runs.router)
api_router.include_router(experiments.router)

_STATUS: dict[type[Exception], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    InvalidRequestError: 422,
}


def install(app: FastAPI) -> None:
    """Mount /api/v1, keep unversioned /health and /ready for probes, register error mapping."""
    app.include_router(health.router, include_in_schema=False)
    app.include_router(api_router)
    for exc_type, code in _STATUS.items():

        async def handler(_req: Request, exc: Exception, code: int = code) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=code)

        app.add_exception_handler(exc_type, handler)
