"""Tasks (a repository + an issue) and starting runs for a task."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query

from swe_agent.api.deps import Auth, SettingsDep, Tasks, validate_source
from swe_agent.schemas.api import RunCreated, RunCreateIn, TaskCreated, TaskCreateIn

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Auth])


@router.post("", status_code=201)
async def create_task(body: TaskCreateIn, tasks: Tasks, settings: SettingsDep) -> TaskCreated:
    return await tasks.create(body, validate_source(body.repo, settings))


@router.get("")
async def list_tasks(tasks: Tasks, limit: int = Query(50, le=500)) -> list[dict[str, Any]]:
    return await tasks.list_tasks(limit)


@router.get("/{task_id}")
async def get_task(task_id: str, tasks: Tasks) -> dict[str, Any]:
    return await tasks.get(task_id)


@router.post("/{task_id}/runs", status_code=202)
async def create_run(
    task_id: str,
    body: RunCreateIn,
    tasks: Tasks,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> RunCreated:
    return await tasks.start_run(task_id, body, idempotency_key)
