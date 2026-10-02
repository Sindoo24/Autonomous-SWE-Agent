"""Runs: live state, trajectory, plan, patches, tests, artifacts, approval decisions, cancel.

Live views (plan, patches, tests, current node) come from the run's LangGraph checkpoint. When
the checkpoint is gone (e.g. a baseline run, or a cleaned-up run) they fall back to the run's
final `report.json`. The trajectory comes from the `events` table; files from the run's
artifact directory.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from swe_agent.agents.runner import RunNotFoundError, read_state
from swe_agent.config import Settings
from swe_agent.core.errors import ConflictError, InvalidRequestError, NotFoundError
from swe_agent.db import store
from swe_agent.db.models import AgentRun
from swe_agent.evaluation.systems import RESUMABLE
from swe_agent.repository.workspace import Workspace
from swe_agent.schemas.api import run_out

_ART_ID = re.compile(r"^art_\d+_[A-Za-z0-9_]+$")
NAMED_FILES = {
    "report.json", "final.patch", "pending.patch", "approval_request.json", "trajectory.jsonl",
    "spans.jsonl",
}  # fmt: skip
MAX_DIFF_CHARS = 200_000
FINISHED = ("finished", "failed", "cancelled")


class RunService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.s = session
        self.settings = settings

    # ------------------------------------------------------------------ lookups

    async def _run(self, run_id: str) -> AgentRun:
        run = await store.get_run(self.s, run_id)
        if run is None:
            raise NotFoundError(f"no run {run_id}")
        return run

    async def _state(self, run_id: str) -> tuple[dict[str, Any], list[str]] | None:
        try:
            return await read_state(run_id, self.settings)
        except RunNotFoundError:
            return None

    async def _state_or_report(self, run: AgentRun) -> dict[str, Any]:
        st = await self._state(run.id)
        if st is None:
            report = _read_json(_art_dir(run), "report.json")
            if report is None:
                raise NotFoundError("no state recorded for this run yet")
            return {"_report": report}
        return st[0]

    # ------------------------------------------------------------------ reads

    async def list_runs(
        self, task_id: str | None, experiment_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        runs = await store.list_runs(self.s, task_id=task_id, experiment_id=experiment_id,
                                     limit=limit)  # fmt: skip
        return [run_out(r) for r in runs]

    async def get(self, run_id: str) -> dict[str, Any]:
        return run_out(await self._run(run_id))

    async def status(self, run_id: str) -> dict[str, Any]:
        run = await self._run(run_id)
        out: dict[str, Any] = {
            "run_id": run.id, "status": run.status, "system": run.system,
            "termination_reason": run.termination_reason,
            "verification_level": run.verification_level,
            "verification_status": run.verification_status,
            "cancel_requested": run.cancel_requested, "error": run.error,
        }  # fmt: skip
        out["last_seq"] = await store.last_event_seq(self.s, run_id)
        st = await self._state(run_id) if run.system in RESUMABLE else None
        if st is not None:
            values, nxt = st
            budget = values.get("budget")
            out |= {
                "current_node": values.get("current_node"),
                "next": nxt,
                "iteration": budget.used_iterations if budget else None,
                "budget": jsonable_encoder(budget),
            }
        return out

    async def trajectory(self, run_id: str, after_seq: int, limit: int) -> dict[str, Any]:
        await self._run(run_id)
        events = await store.events_after(self.s, run_id, after_seq, limit)
        items = [
            {"seq": e.seq, "ts": e.ts, "event": e.event, "node": e.node,
             "iteration": e.iteration, "data": e.data}
            for e in events
        ]  # fmt: skip
        return {"events": items, "next_after_seq": items[-1]["seq"] if items else after_seq}

    async def plan(self, run_id: str) -> dict[str, Any]:
        v = await self._state_or_report(await self._run(run_id))
        if "_report" in v:
            return {"plan": v["_report"].get("plan"), "plan_history": [],
                    "hypotheses": v["_report"].get("hypotheses")}  # fmt: skip
        return _enc({
            "plan": v.get("plan"),
            "plan_history": v.get("plan_history", []),
            "hypotheses": (h.hypotheses if (h := v.get("hypotheses")) else None),
            "findings": v.get("findings"),
            "root_cause_analyses": v.get("root_causes", []),
        })  # fmt: skip

    async def patches(self, run_id: str) -> list[dict[str, Any]]:
        run = await self._run(run_id)
        v = await self._state_or_report(run)
        if "_report" in v:
            return list(v["_report"].get("patch_attempts", []))
        d = _art_dir(run)
        out = []
        for p in v.get("patch_history", []):
            item = jsonable_encoder(p)
            item["diff"] = _artifact_text(d, p.diff_artifact_id, MAX_DIFF_CHARS)
            out.append(item)
        return out

    async def diff(self, run_id: str) -> str:
        """The final (or pending) patch; while running, the live workspace diff."""
        run = await self._run(run_id)
        d = _art_dir(run)
        for name in ("final.patch", "pending.patch"):
            if d is not None and (d / name).is_file():
                return (d / name).read_text(encoding="utf-8")
        st = await self._state(run_id)
        repo = st[0].get("repo") if st else None
        ws_dir = Path(repo.workspace_path) if repo is not None and repo.workspace_path else None
        if repo is not None and ws_dir is not None and await asyncio.to_thread(ws_dir.is_dir):
            ws = Workspace(root=Path(repo.workspace_path), branch=repo.branch,
                           base_commit=repo.base_commit)  # fmt: skip
            text_ = await asyncio.to_thread(ws.diff)
            return text_[:MAX_DIFF_CHARS]
        return ""

    async def tests(self, run_id: str) -> dict[str, Any]:
        v = await self._state_or_report(await self._run(run_id))
        if "_report" in v:
            r = v["_report"]
            return {"baseline": r.get("baseline"), "targets": r.get("targets", []),
                    "test_runs": r.get("tests_executed", []),
                    "verification": r.get("verification"),
                    "reproduction": r.get("reproduction")}  # fmt: skip
        return _enc({
            "baseline": v.get("baseline"),
            "targets": v.get("targets", []),
            "test_runs": [r.model_dump(exclude={"output_tail"}) for r in v.get("test_runs", [])],
            "failure_history": v.get("failure_history", []),
            "verification": v.get("verification"),
            "reproduction": v.get("repro"),
        })  # fmt: skip

    async def report(self, run_id: str) -> dict[str, Any]:
        data = _read_json(_art_dir(await self._run(run_id)), "report.json")
        if data is None:
            raise NotFoundError("no report yet")
        return data

    async def approval_request(self, run_id: str) -> dict[str, Any]:
        run = await self._run(run_id)
        data = _read_json(_art_dir(run), "approval_request.json")
        if run.status != "awaiting_approval" or data is None:
            raise NotFoundError("run is not awaiting approval")
        return data

    async def artifact_path(self, run_id: str, name: str) -> Path:
        """A file in the run's artifact directory: a known name or an artifact id. Anything
        that resolves outside the directory is refused."""
        d = _art_dir(await self._run(run_id))
        if d is None:
            raise NotFoundError("no artifacts yet")
        path: Path | None = None
        if name in NAMED_FILES:
            path = d / name
        elif _ART_ID.match(name):
            path = next(iter(sorted(d.glob(f"{name}.*"))), None) or d / name
        if path is None or not path.is_file() or path.resolve().parent != d.resolve():
            raise NotFoundError(f"no artifact {name}")
        return path

    async def metrics(self, run_id: str) -> dict[str, Any]:
        await self._run(run_id)
        return _enc(await store.run_metrics(self.s, run_id))

    # ------------------------------------------------------------------ decisions

    async def _decide(
        self, run_id: str, approved: bool, feedback: str | None, retry: bool, by: str
    ) -> dict[str, Any]:
        """Record a human decision and enqueue a resume job for the paused graph."""
        async with self.s.begin():
            run = await self._run(run_id)
            if run.status != "awaiting_approval" or await store.active_job(self.s, run_id):
                raise ConflictError(f"run is {run.status}, not awaiting approval")
            decision = {"approved": approved, "feedback": feedback, "retry": retry,
                        "decided_by": by}  # fmt: skip
            await store.record_approval(self.s, run_id, approved, feedback, retry, by)
            await store.enqueue(self.s, run_id, "resume", {"decision": decision})
            await store.set_run(self.s, run_id, status="queued")
        return {"run_id": run_id, "status": "queued", "decision": decision}

    async def approve(self, run_id: str, by: str | None) -> dict[str, Any]:
        return await self._decide(run_id, True, None, False, by or "api")

    async def reject(
        self, run_id: str, feedback: str | None, retry: bool, by: str | None
    ) -> dict[str, Any]:
        if retry and not feedback:
            raise InvalidRequestError("retry needs feedback for the re-plan")
        return await self._decide(run_id, False, feedback, retry, by or "api")

    async def cancel(self, run_id: str) -> dict[str, Any]:
        async with self.s.begin():
            run = await self._run(run_id)
            if run.status in FINISHED:
                raise ConflictError(f"run is already {run.status}")
            await store.set_run(self.s, run_id, cancel_requested=True)
            job = await store.active_job(self.s, run_id)
            if run.status == "queued" and job is not None and job.status == "queued" \
                    and job.kind == "start":  # fmt: skip
                await store.finish_job(self.s, job.id, "done", "cancelled before start")
                await store.set_run(self.s, run_id, status="cancelled",
                                    termination_reason="cancelled")  # fmt: skip
                return {"run_id": run_id, "status": "cancelled"}
            if run.status == "awaiting_approval":
                # Resume the graph so it stops at the next node boundary and writes its report.
                await store.enqueue(self.s, run_id, "resume", {"decision": {
                    "approved": False, "feedback": "cancelled", "retry": False,
                    "decided_by": "cancel"}})  # fmt: skip
                await store.set_run(self.s, run_id, status="queued")
        return {"run_id": run_id, "status": "cancel_requested"}


def _art_dir(run: AgentRun) -> Path | None:
    return Path(run.artifacts_dir) if run.artifacts_dir else None


def _enc(obj: Any) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(obj))


def _read_json(d: Path | None, name: str) -> dict[str, Any] | None:
    if d is None or not (d / name).is_file():
        return None
    data: dict[str, Any] = json.loads((d / name).read_text(encoding="utf-8"))
    return data


def _artifact_text(d: Path | None, art_id: str, limit: int) -> str | None:
    if d is None or not _ART_ID.match(art_id):
        return None
    path = next(iter(sorted(d.glob(f"{art_id}.*"))), None)
    return path.read_text(encoding="utf-8")[:limit] if path else None
