"""Shared plumbing for the baselines: same context, workspace, budget, sandbox and artifacts as
the agent, and a report.json with the agent's field names so the evaluation treats all systems
alike. Baselines have no verification gate: their verdict is always NOT VERIFIED (level 0), and
they are judged only by the held-out tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.runner import (
    RunOutcome,
    _build_context,
    _close,
    budget_from,
    record_run_started,
)
from swe_agent.config import Settings
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.factory import build_provider
from swe_agent.observability.trajectory import EventSink, Stopwatch
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import prepare_workspace
from swe_agent.schemas.state import TerminationReason


class BaselineRun:
    """Context manager-ish helper: owns the provider and the run context."""

    def __init__(
        self,
        system: str,
        source: str,
        issue: str,
        settings: Settings,
        *,
        provider: LLMProvider | None,
        task_id: str,
        run_id: str,
        sinks: Sequence[EventSink],
        cancel: Callable[[], bool] | None,
    ) -> None:
        self.system = system
        self.source = source
        self.issue = issue
        self.settings = settings
        self.owns_provider = provider is None
        self.provider = provider or build_provider(settings.model)
        self.ctx: RunContext = _build_context(
            settings, task_id, run_id, self.provider, budget_from(settings), sinks=sinks,
            cancel=cancel,
        )  # fmt: skip
        self.errors: list[dict[str, str]] = []
        self.clock = Stopwatch()
        self._node_clock = Stopwatch()
        self._open = False
        record_run_started(self.ctx, source, self.provider, system=system)

    def node(self, name: str) -> None:
        rec = self.ctx.recorder
        rec.current_node = name
        rec.record("node_started")
        self._node_clock = Stopwatch()
        self._open = True

    def node_done(self, status: str = "ok") -> None:
        self.ctx.recorder.record("node_finished", status=status, duration_ms=self._node_clock.ms)
        self._open = False

    def prepare(self) -> None:
        self.node("prepare_repo")
        dest = self.settings.workspace_root.resolve() / self.ctx.task_id / self.ctx.run_id / "repo"
        ws = prepare_workspace(self.source, dest, task_id=self.ctx.task_id)
        self.ctx.workspace = ws
        self.ctx.index = SymbolIndex.build(ws.root, ws.ls_files())
        self.node_done()

    def error(self, kind: str, message: str) -> None:
        self.errors.append({"kind": kind, "message": message[:2000]})

    def finish(
        self,
        termination: TerminationReason,
        *,
        iterations: int,
        extra: dict[str, Any] | None = None,
    ) -> RunOutcome:
        ctx = self.ctx
        if self._open:
            self.node_done(termination.value)
        ws = ctx.workspace
        diff = ws.diff() if ws is not None else ""
        stats = ws.numstat() if ws is not None and diff.strip() else []
        files = [path for _, _, path in stats]
        added = sum(a for a, _, _ in stats)
        removed = sum(r for _, r, _ in stats)
        if termination is TerminationReason.PATCH_PROPOSED and not diff.strip():
            termination = TerminationReason.NO_CHANGES
        art = ctx.artifacts.root
        if diff.strip():
            (art / "final.patch").write_text(diff, encoding="utf-8")
        budget = ctx.meter.snapshot()
        report: dict[str, Any] = {
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
            "system": self.system,
            "status": "NOT VERIFIED",
            "verification": {
                "status": "NOT VERIFIED",
                "level": 0,
                "evidence": [],
                "notes": [
                    f"{self.system}: baseline without a verification gate; judged only "
                    "by held-out tests"
                ],
            },
            "termination_reason": termination.value,
            "issue": self.issue,
            "repository": {
                "source": self.source,
                "workspace_path": str(ws.root) if ws else None,
            },
            "files_changed": files,
            "lines_added": added,
            "lines_removed": removed,
            "iterations": iterations,
            "errors": self.errors,
            "budget": budget.model_dump(),
            "trace": ctx.recorder.summary(),
            "latency_s": round(budget.elapsed_seconds, 2),
            "trajectory_file": str(ctx.recorder.path),
            **(extra or {}),
        }
        (art / "report.json").write_text(json.dumps(report, indent=2, default=str),
                                         encoding="utf-8")  # fmt: skip
        ctx.recorder.current_node = None
        ctx.recorder.record("run_finished", termination_reason=termination.value,
                            status="NOT VERIFIED")  # fmt: skip
        state: dict[str, Any] = {"task_id": ctx.task_id, "run_id": ctx.run_id}
        return RunOutcome(ctx.task_id, ctx.run_id, state, report, art)

    async def close(self) -> None:
        if self.owns_provider:
            await self.provider.aclose()
        _close(self.ctx)
