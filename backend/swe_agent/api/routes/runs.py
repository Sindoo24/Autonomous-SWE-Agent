"""Runs: status, trajectory, plan, patches, diff, tests, report, artifacts, decisions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, PlainTextResponse

from swe_agent.api.deps import Auth, Runs
from swe_agent.schemas.api import ApproveIn, RejectIn

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Auth])


@router.get("")
async def list_runs(
    runs: Runs,
    task_id: str | None = None,
    experiment_id: str | None = None,
    limit: int = Query(50, le=1000),
) -> list[dict[str, Any]]:
    return await runs.list_runs(task_id, experiment_id, limit)


@router.get("/{run_id}")
async def get_run(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.get(run_id)


@router.get("/{run_id}/status")
async def run_status(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.status(run_id)


@router.get("/{run_id}/trajectory")
async def trajectory(
    run_id: str, runs: Runs, after_seq: int = 0, limit: int = Query(500, le=5000)
) -> dict[str, Any]:
    return await runs.trajectory(run_id, after_seq, limit)


@router.get("/{run_id}/plan")
async def plan(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.plan(run_id)


@router.get("/{run_id}/patches")
async def patches(run_id: str, runs: Runs) -> list[dict[str, Any]]:
    return await runs.patches(run_id)


@router.get("/{run_id}/diff", response_class=PlainTextResponse)
async def diff(run_id: str, runs: Runs) -> PlainTextResponse:
    return PlainTextResponse(await runs.diff(run_id), media_type="text/x-diff")


@router.get("/{run_id}/tests")
async def tests(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.tests(run_id)


@router.get("/{run_id}/report")
async def report(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.report(run_id)


@router.get("/{run_id}/approval")
async def approval_request(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.approval_request(run_id)


@router.get("/{run_id}/artifacts/{name}")
async def artifact(run_id: str, name: str, runs: Runs) -> FileResponse:
    path = await runs.artifact_path(run_id, name)
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@router.get("/{run_id}/metrics")
async def run_metrics(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.metrics(run_id)


@router.post("/{run_id}/approve", status_code=202)
async def approve(run_id: str, body: ApproveIn, runs: Runs) -> dict[str, Any]:
    return await runs.approve(run_id, body.decided_by)


@router.post("/{run_id}/reject", status_code=202)
async def reject(run_id: str, body: RejectIn, runs: Runs) -> dict[str, Any]:
    return await runs.reject(run_id, body.feedback, body.retry, body.decided_by)


@router.post("/{run_id}/cancel", status_code=202)
async def cancel(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.cancel(run_id)
