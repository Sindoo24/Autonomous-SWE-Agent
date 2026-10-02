"""Experiments (read-only; they are created by `swe-agent experiment run`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from swe_agent.api.deps import Auth, Experiments

router = APIRouter(prefix="/experiments", tags=["experiments"], dependencies=[Auth])


@router.get("")
async def list_experiments(experiments: Experiments) -> list[dict[str, Any]]:
    return await experiments.list_experiments()


@router.get("/{exp_id}/metrics")
async def experiment_metrics(exp_id: str, experiments: Experiments) -> dict[str, Any]:
    return await experiments.metrics(exp_id)
