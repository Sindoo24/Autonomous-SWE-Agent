"""Run, pause, resume and continue tasks. Used by the CLI and by the worker.

Every run is checkpointed (thread_id = run_id): to PostgreSQL when SWE_DATABASE_URL is set,
otherwise to SQLite. A run that stops for human approval is continued with `resume_run`; a run
whose process died mid-way is continued from its last completed node with `continue_run`.
Services (workspace handle, symbol index, sandbox, model gateway) are not checkpointed; they are
rebuilt from the checkpointed state.
"""

from __future__ import annotations

import enum
import inspect
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from swe_agent.agents.context import RunContext
from swe_agent.agents.graph import build_graph
from swe_agent.agents.state import AgentState
from swe_agent.config import Settings
from swe_agent.core.budget import BudgetMeter
from swe_agent.core.ids import new_id
from swe_agent.db.engine import is_postgres, libpq_url
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.factory import build_provider, gen_params
from swe_agent.llm.gateway import LLMGateway, ModelRole, RoleBinding
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.otel import span_sink_for
from swe_agent.observability.trajectory import EventSink, TrajectoryRecorder
from swe_agent.prompts.templates import PROMPT_VERSION
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import Workspace
from swe_agent.sandbox.service import SandboxService
from swe_agent.schemas import state as state_models
from swe_agent.schemas.state import ApprovalDecision, Budget, RepoRef
from swe_agent.tools.executor import ToolExecutor

RECURSION_LIMIT = 250


@dataclass
class RunOutcome:
    task_id: str
    run_id: str
    state: dict[str, Any]
    report: dict[str, Any]
    artifacts_dir: Path
    # Set when the run is paused at human approval (the request shown to the reviewer).
    pending_approval: dict[str, Any] | None = None


class RunNotFoundError(LookupError):
    pass


# --------------------------------------------------------------------------- checkpointer


def _state_types() -> list[tuple[str, str]]:
    """Explicit allow-list of our state classes for checkpoint deserialisation: only these (plus
    LangGraph's own types) are ever reconstructed from the checkpoint database."""
    out = []
    for name, obj in vars(state_models).items():
        if (
            inspect.isclass(obj)
            and obj.__module__ == state_models.__name__
            and issubclass(obj, BaseModel | enum.Enum)
        ):
            out.append((state_models.__name__, name))
    return out


_PG_SETUP_DONE: set[str] = set()


def checkpoint_location(settings: Settings) -> str:
    """Where this process looks for checkpoints (for error messages): a run can only be resumed
    by a process configured with the same backend."""
    if is_postgres(settings.database_url):
        return "PostgreSQL (SWE_DATABASE_URL)"
    return f"SQLite {settings.checkpoint_db.resolve()} (SWE_DATABASE_URL not set)"


async def setup_checkpoints(settings: Settings) -> None:
    """Create / migrate the checkpoint tables (idempotent)."""
    async with open_checkpointer(settings):
        pass


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    serde = JsonPlusSerializer(allowed_msgpack_modules=_state_types())
    if is_postgres(settings.database_url):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        url = libpq_url(settings.database_url or "")
        async with AsyncPostgresSaver.from_conn_string(url, serde=serde) as pg:
            # Checkpoint-table migrations run once per process (and at API start / `db upgrade`),
            # never inside a request: they use CREATE INDEX CONCURRENTLY, which waits for every
            # open transaction.
            if url not in _PG_SETUP_DONE:
                await pg.setup()
                _PG_SETUP_DONE.add(url)
            yield pg
        return
    path = settings.checkpoint_db.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as conn:
        saver = AsyncSqliteSaver(conn, serde=serde)
        await saver.setup()
        yield saver


def _graph(settings: Settings, saver: Any) -> Any:
    return build_graph(
        max_validation_attempts=settings.budget.max_validation_attempts,
        execution=settings.sandbox.enabled,
        checkpointer=saver,
    )


# --------------------------------------------------------------------------- context


CancelCheck = Callable[[], bool]


def _build_context(
    settings: Settings,
    task_id: str,
    run_id: str,
    provider: LLMProvider,
    budget: Budget,
    *,
    sinks: Sequence[EventSink] = (),
    cancel: CancelCheck | None = None,
) -> RunContext:
    art_dir = settings.artifacts_root.resolve() / task_id / run_id
    artifacts = ArtifactStore(art_dir)
    span_sink = span_sink_for(settings, task_id, run_id)
    all_sinks: list[EventSink] = list(sinks)
    if span_sink is not None:
        all_sinks.append(span_sink)
    recorder = TrajectoryRecorder(task_id, run_id, art_dir / "trajectory.jsonl", sinks=all_sinks)
    meter = BudgetMeter(budget)
    m = settings.model
    gateway = LLMGateway(
        {
            ModelRole.REASONING: RoleBinding(provider, m.reasoning, m.tool_mode),
            ModelRole.CODER: RoleBinding(provider, m.coder, m.tool_mode),
        },
        gen_params(m),
        meter,
        recorder,
        artifacts,
    )
    executor = ToolExecutor(
        meter, recorder, artifacts, max_output_chars=settings.tool_output_max_chars
    )
    sandbox = None
    if settings.sandbox.enabled:
        sandbox = SandboxService(
            settings.sandbox,
            artifacts,
            recorder,
            scratch_root=settings.workspace_root.resolve() / task_id / run_id / "sandbox",
            run_label=run_id,
        )
    ctx = RunContext(
        settings=settings,
        task_id=task_id,
        run_id=run_id,
        artifacts=artifacts,
        recorder=recorder,
        meter=meter,
        gateway=gateway,
        executor=executor,
        sandbox=sandbox,
    )
    ctx.span_sink = span_sink
    if cancel is not None:
        ctx.cancel_requested = cancel
    return ctx


def _close(ctx: RunContext | None) -> None:
    if ctx is None:
        return
    if ctx.sandbox is not None:
        ctx.sandbox.cleanup()
    if ctx.span_sink is not None:
        ctx.span_sink.close()


def budget_from(settings: Settings) -> Budget:
    b = settings.budget
    return Budget(
        max_iterations=b.max_iterations,
        max_tool_calls=b.max_tool_calls,
        max_tokens=b.max_tokens,
        max_wall_seconds=b.max_wall_seconds,
    )


def record_run_started(ctx: RunContext, source: str, provider: LLMProvider, **extra: Any) -> None:
    m = ctx.settings.model
    ctx.recorder.record(
        "run_started",
        source=source,
        provider=provider.name,
        models={"reasoning": m.reasoning, "coder": m.coder},
        tool_mode=m.tool_mode.value,
        temperature=m.temperature,
        seed=m.seed,
        prompt_version=PROMPT_VERSION,
        execution=ctx.settings.sandbox.enabled,
        sandbox_base_image=(
            ctx.settings.sandbox.base_image if ctx.settings.sandbox.enabled else None
        ),
        agent=ctx.settings.agent.model_dump(mode="json"),
        **extra,
    )


def _config(ctx: RunContext) -> dict[str, Any]:
    return {
        "configurable": {"ctx": ctx, "thread_id": ctx.run_id},
        "recursion_limit": RECURSION_LIMIT,
    }


async def _finish(
    graph: Any, ctx: RunContext, final: dict[str, Any], *, record: bool = True
) -> RunOutcome:
    snap = await graph.aget_state({"configurable": {"thread_id": ctx.run_id}})
    art_dir = ctx.artifacts.root
    if snap.next:  # paused at an interrupt (human approval)
        request = next(
            (i.value for t in snap.tasks for i in (t.interrupts or ())), {"run_id": ctx.run_id}
        )
        if record:
            ctx.recorder.record("run_finished", status="awaiting_approval")
        return RunOutcome(ctx.task_id, ctx.run_id, dict(snap.values), {}, art_dir, request)
    report_path = art_dir / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if not record:
        return RunOutcome(ctx.task_id, ctx.run_id, final, report, art_dir)
    ctx.recorder.record(
        "run_finished",
        termination_reason=str(final.get("termination_reason")),
        status=report.get("status"),
    )
    return RunOutcome(ctx.task_id, ctx.run_id, final, report, art_dir)


# --------------------------------------------------------------------------- public API


async def run_task(
    source: str,
    issue: str,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    sinks: Sequence[EventSink] = (),
    cancel: CancelCheck | None = None,
) -> RunOutcome:
    task_id = task_id or new_id("t")
    run_id = run_id or new_id("r")
    owns_provider = provider is None
    prov = provider or build_provider(settings.model)
    ctx = _build_context(
        settings, task_id, run_id, prov, budget_from(settings), sinks=sinks, cancel=cancel
    )
    record_run_started(ctx, source, prov)
    init: AgentState = {
        "task_id": task_id,
        "run_id": run_id,
        "repo": RepoRef(source=source),
        # The initial state is checkpointed before `intake` validates it. U+0000 can never be
        # stored by PostgreSQL (text or JSONB), so it is dropped here; `intake` still strips the
        # other control characters and rejects an issue that is empty afterwards.
        "issue": issue.replace("\x00", ""),
    }
    try:
        async with open_checkpointer(settings) as saver:
            graph = _graph(settings, saver)
            final: dict[str, Any] = await graph.ainvoke(init, config=_config(ctx))
            return await _finish(graph, ctx, final)
    finally:
        if owns_provider:
            await prov.aclose()
        _close(ctx)


async def read_state(run_id: str, settings: Settings) -> tuple[dict[str, Any], list[str]]:
    """The run's latest checkpointed state and the nodes it will run next ([] when finished).
    Raises RunNotFoundError when there is no checkpoint."""
    async with open_checkpointer(settings) as saver:
        snap = await _graph(settings, saver).aget_state({"configurable": {"thread_id": run_id}})
    if not snap.values:
        raise RunNotFoundError(run_id)
    return dict(snap.values), list(snap.next)


async def run_status(run_id: str, settings: Settings) -> dict[str, Any]:
    async with open_checkpointer(settings) as saver:
        snap = await _graph(settings, saver).aget_state({"configurable": {"thread_id": run_id}})
    if not snap.values:
        raise RunNotFoundError(run_id)
    v = snap.values
    reason = v.get("termination_reason")
    ver = v.get("verification")
    return {
        "run_id": run_id,
        "task_id": v.get("task_id"),
        "state": "awaiting_approval" if snap.next else ("finished" if reason else "interrupted"),
        "next": list(snap.next),
        "current_node": v.get("current_node"),
        "termination_reason": getattr(reason, "value", reason),
        "verification": ver.model_dump(mode="json") if ver else None,
        "artifacts_dir": str(settings.artifacts_root.resolve() / v["task_id"] / run_id),
    }


def _restore(
    settings: Settings,
    v: dict[str, Any],
    run_id: str,
    prov: LLMProvider,
    sinks: Sequence[EventSink],
    cancel: CancelCheck | None,
) -> RunContext:
    ctx = _build_context(settings, v["task_id"], run_id, prov, v["budget"], sinks=sinks,
                         cancel=cancel)  # fmt: skip
    repo: RepoRef = v["repo"]
    if repo.workspace_path:  # absent only if the run died inside prepare_repo
        ctx.workspace = Workspace(
            root=Path(repo.workspace_path), branch=repo.branch, base_commit=repo.base_commit
        )
        ctx.index = SymbolIndex.build(ctx.workspace.root, ctx.workspace.ls_files())
    return ctx


async def resume_run(
    run_id: str,
    decision: ApprovalDecision,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    sinks: Sequence[EventSink] = (),
    cancel: CancelCheck | None = None,
) -> RunOutcome:
    """Continue a run paused at human approval, from any process. On approve this only runs
    finalize (no model calls); on reject-with-retry it continues into re-planning."""
    owns_provider = provider is None
    prov = provider or build_provider(settings.model)
    ctx: RunContext | None = None
    try:
        async with open_checkpointer(settings) as saver:
            graph = _graph(settings, saver)
            snap = await graph.aget_state({"configurable": {"thread_id": run_id}})
            if not snap.values:
                raise RunNotFoundError(run_id)
            if "human_approval" not in snap.next:
                raise RunNotFoundError(f"run {run_id} is not awaiting approval")
            ctx = _restore(settings, dict(snap.values), run_id, prov, sinks, cancel)
            ctx.recorder.record("approval", action="resumed", decision=decision.model_dump())
            final = await graph.ainvoke(Command(resume=decision.model_dump()), config=_config(ctx))
            return await _finish(graph, ctx, final)
    finally:
        if owns_provider:
            await prov.aclose()
        _close(ctx)


async def continue_run(
    run_id: str,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    sinks: Sequence[EventSink] = (),
    cancel: CancelCheck | None = None,
) -> RunOutcome | None:
    """Continue a run whose process died, from its last checkpoint (the node that was running
    when it died is re-executed). Returns None when there is no checkpoint yet (start it
    again instead). A run paused at approval or already finished is reported as is."""
    owns_provider = provider is None
    prov = provider or build_provider(settings.model)
    ctx: RunContext | None = None
    try:
        async with open_checkpointer(settings) as saver:
            graph = _graph(settings, saver)
            snap = await graph.aget_state({"configurable": {"thread_id": run_id}})
            if not snap.values or "task_id" not in snap.values:
                return None
            ctx = _restore(settings, dict(snap.values), run_id, prov, sinks, cancel)
            waiting = any(t.interrupts for t in snap.tasks)
            if not snap.next or waiting:
                return await _finish(graph, ctx, dict(snap.values), record=False)
            ctx.recorder.record("run_continued", after_node=snap.values.get("current_node"),
                                next=list(snap.next))  # fmt: skip
            final = await graph.ainvoke(None, config=_config(ctx))
            return await _finish(graph, ctx, final)
    finally:
        if owns_provider:
            await prov.aclose()
        _close(ctx)
