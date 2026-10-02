"""Readiness checks: database, Docker (when the sandbox is enabled) and the model endpoint."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from swe_agent.config import Settings
from swe_agent.db import store


async def readiness(
    settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    try:
        async with sessions() as s:
            await store.ping(s)
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"[:200]
    if settings.sandbox.enabled:
        from swe_agent.sandbox.docker import docker_available

        ok, info = await asyncio.to_thread(docker_available, settings.sandbox.docker_bin)
        checks["docker"] = "ok" if ok else f"error: {info}"[:200]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get(settings.model.base_url)
        checks["model_endpoint"] = "ok"
    except httpx.HTTPError as exc:
        checks["model_endpoint"] = f"error: {type(exc).__name__}"
    return all(v == "ok" for v in checks.values()), checks
