"""Baseline B: ReAct.

One tool loop: the model gets the issue and every tool (read, search, git, edit, run_tests) and
acts until it calls `finish` or the budget ends. No explicit plan, reproduction step, failure
classifier or verification gate. Same model, sandbox, write policy (tests read-only) and budget as
the agent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

from swe_agent.agents.loop import run_tool_loop
from swe_agent.agents.nodes.patching import IMPLEMENT_POLICY
from swe_agent.agents.runner import RunOutcome
from swe_agent.config import Settings
from swe_agent.core.errors import BudgetExceeded, ModelError, RepoError
from swe_agent.core.ids import new_id
from swe_agent.evaluation.baselines.common import BaselineRun
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.gateway import ModelRole
from swe_agent.observability.trajectory import EventSink
from swe_agent.prompts import baselines as P
from swe_agent.sandbox.docker import EnvSetupError
from swe_agent.schemas.state import TerminationReason, TestRun, TestRunKind
from swe_agent.tools.base import ToolContext

FAILURES_SHOWN = 4


class ReactFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(description="What was wrong and what you changed")


def render_run(run: TestRun) -> str:
    lines = [
        f"status: {run.status.value}  passed: {len(run.passed)}  failed: {len(run.failed)}  "
        f"errors: {len(run.errors)}  collection errors: {len(run.collection_errors)}"
    ]
    for f in run.failures[:FAILURES_SHOWN]:
        lines.append(f"--- {f.nodeid} ({f.kind}): {f.message}\n{f.excerpt[-800:]}")
    if not run.failures and run.status.value != "passed":
        lines.append(run.output_tail[-1500:])
    return "\n".join(lines)


async def run_react(
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
    b = BaselineRun("baseline_react", source, issue, settings, provider=provider,
                    task_id=task_id or new_id("t"), run_id=run_id or new_id("r"), sinks=sinks,
                    cancel=cancel)  # fmt: skip
    ctx = b.ctx
    termination = TerminationReason.PATCH_PROPOSED
    extra: dict[str, object] = {}
    test_runs: list[TestRun] = []
    edits_before_test = [0]
    try:
        b.prepare()
        ws, index = ctx.require_workspace()
        image: list[str] = []

        def test_runner(selection: list[str]) -> str:
            if ctx.sandbox is None:
                return "ERROR: test execution is disabled (SANDBOX_ENABLED=false)"
            try:
                if not image:
                    image.append(ctx.sandbox.prepare_image(ws)[0])
                run = ctx.sandbox.run_tests(ws, image[0], TestRunKind.TARGET,
                                            selection=selection or None)  # fmt: skip
            except (EnvSetupError, ValueError) as exc:
                return f"ERROR: could not run tests: {str(exc)[-1500:]}"
            test_runs.append(run)
            edits_before_test.append(len(tool_ctx.edits))
            return render_run(run)

        tool_ctx = ToolContext(workspace=ws, index=index, node="react",
                               write_policy=IMPLEMENT_POLICY, test_runner=test_runner)  # fmt: skip
        b.node("react")
        if cancel is not None and cancel():
            termination = TerminationReason.CANCELLED
        else:
            done = await run_tool_loop(
                ctx,
                node="react",
                role=ModelRole.CODER,
                system=P.REACT_SYSTEM,
                task=P.react_task(issue),
                finish_model=ReactFinish,
                finish_description="Finish: summarise the fix.",
                max_steps=settings.budget.max_tool_calls,
                tool_ctx=tool_ctx,
            )
            extra["summary"] = done.summary
        b.node_done()
    except BudgetExceeded as exc:
        b.error("budget_exhausted", str(exc))
        termination = TerminationReason.BUDGET_EXHAUSTED
    except ModelError as exc:
        b.error("model_error", str(exc))
        termination = TerminationReason.MODEL_ERROR
    except RepoError as exc:
        b.error("repo_error", str(exc))
        termination = TerminationReason.REPO_ERROR
    # "iterations" for ReAct = test runs made after new edits (edit -> test cycles), at least 1.
    cycles = sum(1 for a, b_ in pairwise(edits_before_test) if b_ > a)
    extra["tests_executed"] = [
        {"id": r.id, "status": r.status.value, "passed": len(r.passed), "failed": len(r.failed),
         "errors": len(r.errors)}
        for r in test_runs
    ]  # fmt: skip
    try:
        return b.finish(termination, iterations=max(1, cycles), extra=extra)
    finally:
        await b.close()
