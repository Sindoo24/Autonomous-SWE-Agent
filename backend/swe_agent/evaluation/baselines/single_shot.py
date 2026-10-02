"""Baseline A: single-shot.

Deterministic retrieval with the agent's own candidate ranker -> the top files' full text -> ONE
model call returning search/replace edits (plus the gateway's single repair retry for malformed
JSON, which every system gets) -> edits applied with the same edit tool and write policy as the
agent. No tests are run, nothing is retried.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from swe_agent.agents.nodes.patching import IMPLEMENT_POLICY
from swe_agent.agents.runner import RunOutcome
from swe_agent.config import Settings
from swe_agent.core.errors import BudgetExceeded, ModelError, RepoError
from swe_agent.core.ids import new_id
from swe_agent.evaluation.baselines.common import BaselineRun
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.types import Message
from swe_agent.observability.trajectory import EventSink
from swe_agent.prompts import baselines as P
from swe_agent.repository.pathjail import is_test_path
from swe_agent.repository.summary import build_summary
from swe_agent.schemas.state import TerminationReason
from swe_agent.tools.base import ToolContext

TOP_K = 5


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    search: str = Field(min_length=1)
    replace: str


class SingleShotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str
    edits: list[Edit] = Field(min_length=1, max_length=10)


async def run_single_shot(
    source: str,
    issue: str,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    sinks: Sequence[EventSink] = (),
    cancel: Callable[[], bool] | None = None,
) -> RunOutcome:
    b = BaselineRun("baseline_single_shot", source, issue, settings, provider=provider,
                    task_id=task_id or new_id("t"), run_id=run_id or new_id("r"), sinks=sinks,
                    cancel=cancel)  # fmt: skip
    ctx = b.ctx
    termination = TerminationReason.PATCH_PROPOSED
    extra: dict[str, object] = {}
    try:
        b.prepare()
        ws, index = ctx.require_workspace()

        b.node("retrieve")
        files = ws.ls_files()
        summary = build_summary(ws.root, files, index, issue)
        budget_chars = max(4000, settings.loop_context_max_chars // 2)
        chosen: list[tuple[str, str]] = []
        used = 0
        for cand in summary.candidates:
            if len(chosen) >= TOP_K or is_test_path(cand.path) or not cand.path.endswith(".py"):
                continue
            text = ws.jail.resolve(cand.path, must_exist=True).read_text(
                encoding="utf-8", errors="replace"
            )
            text = text[: max(0, budget_chars - used)]
            if not text:
                break
            chosen.append((cand.path, text))
            used += len(text)
        b.node_done()
        extra["retrieved_files"] = [p for p, _ in chosen]

        b.node("single_shot")
        paths = {p for p, _ in chosen}

        def check(patch: SingleShotPatch) -> list[str]:
            return [f"edits[{i}].path {e.path!r} is not one of the provided files"
                    for i, e in enumerate(patch.edits) if e.path not in paths]  # fmt: skip

        if cancel is not None and cancel():
            raise _Cancelled
        patch = await ctx.gateway.structured(
            ModelRole.CODER,
            [
                Message(role="system", content=P.SINGLE_SHOT_SYSTEM),
                Message(role="user", content=P.single_shot_task(issue, chosen)),
            ],
            SingleShotPatch,
            purpose="single_shot",
            validate=check,
        )
        tool_ctx = ToolContext(workspace=ws, index=index, node="implement",
                               write_policy=IMPLEMENT_POLICY)  # fmt: skip
        applied = 0
        for e in patch.edits:
            res = await ctx.executor.invoke("edit_file", e.model_dump(), tool_ctx)
            applied += int(res.ok)
            if not res.ok:
                b.error("edit_failed", res.output)
        extra |= {"explanation": patch.explanation, "edits_proposed": len(patch.edits),
                  "edits_applied": applied}  # fmt: skip
        b.node_done()
    except _Cancelled:
        termination = TerminationReason.CANCELLED
    except BudgetExceeded as exc:
        b.error("budget_exhausted", str(exc))
        termination = TerminationReason.BUDGET_EXHAUSTED
    except ModelError as exc:
        b.error("model_error", str(exc))
        termination = TerminationReason.MODEL_ERROR
    except RepoError as exc:
        b.error("repo_error", str(exc))
        termination = TerminationReason.REPO_ERROR
    try:
        return b.finish(termination, iterations=1, extra=extra)
    finally:
        await b.close()


class _Cancelled(Exception):
    pass
