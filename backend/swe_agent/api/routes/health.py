"""Liveness and readiness probes (no auth)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from swe_agent.api.deps import SettingsDep
from swe_agent.services.health_service import readiness

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, settings: SettingsDep) -> dict[str, Any]:
    ok, checks = await readiness(settings, request.app.state.sessions)
    if not ok:
        raise HTTPException(503, {"ready": False, "checks": checks})
    return {"ready": True, "checks": checks}
