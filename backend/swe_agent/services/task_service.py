"""Tasks (repository + issue) and starting runs for them."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from swe_agent.config import OverrideError, check_overrides
from swe_agent.core.errors import InvalidRequestError, NotFoundError
from swe_agent.db import store
from swe_agent.schemas.api import RunCreated, RunCreateIn, TaskCreated, TaskCreateIn, run_out


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(self, body: TaskCreateIn, source: str) -> TaskCreated:
        """`source` has already been checked against the repository policy by the caller."""
        async with self.s.begin():
            task = await store.create_task(self.s, source, body.issue, created_by=body.created_by,
                                           budget=body.budget)  # fmt: skip
        return TaskCreated(task_id=task.id, repository=source)

    async def list_tasks(self, limit: int) -> list[dict[str, Any]]:
        return await store.list_tasks(self.s, limit)

    async def get(self, task_id: str) -> dict[str, Any]:
        found = await store.task_with_source(self.s, task_id)
        if found is None:
            raise NotFoundError(f"no task {task_id}")
        task, source = found
        runs = await store.list_runs(self.s, task_id=task_id)
        return {
            "id": task.id,
            "repository": source,
            "issue": task.issue_text,
            "created_at": task.created_at,
            "benchmark_task_id": task.benchmark_task_id,
            "runs": [run_out(r) for r in runs],
        }

    async def start_run(
        self, task_id: str, body: RunCreateIn, idempotency_key: str | None
    ) -> RunCreated:
        """Create a run and enqueue its start job for the worker (one transaction)."""
        overrides = body.effective_overrides()
        try:
            check_overrides(overrides)
        except OverrideError as exc:
            raise InvalidRequestError(str(exc)) from exc
        async with self.s.begin():
            if await store.task_with_source(self.s, task_id) is None:
                raise NotFoundError(f"no task {task_id}")
            run, created = await store.create_run(
                self.s, task_id, system=body.system, variant=body.variant, overrides=overrides,
                idempotency_key=idempotency_key,
            )  # fmt: skip
        return RunCreated(run_id=run.id, created=created, status=run.status)
