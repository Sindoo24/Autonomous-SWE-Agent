"""Shared node plumbing: trajectory events, error -> termination mapping, budget snapshot."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from swe_agent.agents.context import RunContext, get_ctx
from swe_agent.agents.state import AgentState
from swe_agent.core.errors import BudgetExceeded, ModelError, RepoError
from swe_agent.observability.trajectory import Stopwatch
from swe_agent.repository.workspace import Workspace
from swe_agent.schemas.state import AgentError, EvidenceRef, TerminationReason

# Terminal nodes always run, so a cancelled run still writes its report.
UNCANCELLABLE = frozenset({"finalize", "report_failure"})

NodeFn = Callable[[AgentState, RunContext], Awaitable[dict[str, Any]]]


def node(
    name: str,
) -> Callable[[NodeFn], Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]]:
    def deco(fn: NodeFn) -> Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]:
        async def wrapper(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
            ctx = get_ctx(config)
            ctx.recorder.current_node = name
            ctx.recorder.current_iteration = ctx.meter.budget.used_iterations or None
            sw = Stopwatch()
            ctx.recorder.record("node_started")
            status = "ok"
            try:
                if name not in UNCANCELLABLE and ctx.cancel_requested():
                    status = "cancelled"
                    update = _fail(
                        name, "cancelled", "cancel requested", TerminationReason.CANCELLED
                    )
                else:
                    update = await fn(state, ctx)
            except BudgetExceeded as exc:
                status = "budget_exhausted"
                update = _fail(
                    name, "budget_exhausted", str(exc), TerminationReason.BUDGET_EXHAUSTED
                )
            except ModelError as exc:
                status = "model_error"
                update = _fail(name, "model_error", str(exc), TerminationReason.MODEL_ERROR)
            except RepoError as exc:
                status = "repo_error"
                update = _fail(name, "repo_error", str(exc), TerminationReason.REPO_ERROR)
            update["current_node"] = name
            update["budget"] = ctx.meter.snapshot()
            ctx.recorder.record(
                "node_finished",
                status=status,
                duration_ms=sw.ms,
                termination_reason=update.get("termination_reason"),
            )
            return update

        # Not functools.wraps: LangGraph inspects the signature to decide whether to pass
        # `config`, and wraps' __wrapped__ would expose the inner (state, ctx) signature.
        wrapper.__name__ = fn.__name__
        wrapper.__qualname__ = fn.__qualname__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


def _fail(node_name: str, kind: str, msg: str, reason: TerminationReason) -> dict[str, Any]:
    return {
        "errors": [AgentError(node=node_name, kind=kind, message=msg[:2000])],
        "termination_reason": reason,
    }


def check_evidence(ws: Workspace, refs: list[EvidenceRef]) -> list[str]:
    """Anti-hallucination: every cited file must exist and the line range must be real."""
    errors: list[str] = []
    for i, r in enumerate(refs):
        try:
            p = ws.jail.resolve(r.path, must_exist=True)
        except Exception as exc:
            errors.append(f"evidence[{i}] {r.path}: {exc}")
            continue
        if not p.is_file():
            errors.append(f"evidence[{i}] {r.path}: not a file")
            continue
        n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        if r.start_line > r.end_line:
            errors.append(f"evidence[{i}] {r.path}: start_line > end_line")
        elif r.start_line > n:
            errors.append(f"evidence[{i}] {r.path}: line {r.start_line} beyond end of file ({n})")
    return errors


def render_evidence(
    ws: Workspace, refs: list[EvidenceRef], *, pad: int = 3, max_chars: int = 8000
) -> str:
    """Numbered code at each evidence range (merged per file), for single-shot LLM stages."""
    by_file: dict[str, list[tuple[int, int]]] = {}
    for r in refs:
        by_file.setdefault(r.path, []).append((r.start_line, r.end_line))
    blocks: list[str] = []
    used = 0
    for rel, ranges in by_file.items():
        try:
            p = ws.jail.resolve(rel, must_exist=True)
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:  # noqa: S112 - invalid refs are reported by check_evidence
            continue
        ranges.sort()
        merged: list[list[int]] = []
        for s, e in ranges:
            s, e = max(1, s - pad), min(len(lines), e + pad)
            if merged and s <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        for s, e in merged:
            body = "\n".join(f"{i}| {lines[i - 1]}" for i in range(s, e + 1))
            block = f"--- {rel} lines {s}-{e}\n{body}"
            if used + len(block) > max_chars:
                blocks.append(f"--- {rel} lines {s}-{e} (omitted: context limit)")
                continue
            blocks.append(block)
            used += len(block)
    return "\n".join(blocks) or "(no code available)"


def artifact_dir(ctx: RunContext) -> Path:
    return ctx.artifacts.root
