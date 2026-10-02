"""`analyze_failure`: decide what happens after a failed test run.

run_tests --fail--> analyze_failure --> implement | plan | explore (after rollback)
                                    --> report_failure (unrecoverable, budget, stagnation)"""

from __future__ import annotations

import asyncio
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import _fail, node
from swe_agent.agents.state import AgentState
from swe_agent.core.errors import ModelError
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.types import Message
from swe_agent.prompts import templates as T
from swe_agent.prompts.safety import neutralize
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.schemas.state import (
    FailureAnalysis,
    FailureCategory,
    RootCauseAnalysis,
    TerminationReason,
    TestRun,
    TestRunKind,
)

FEEDBACK_MAX_CHARS = 6000


def failure_feedback(analysis: FailureAnalysis, run: TestRun | None) -> str:
    lines = [f"category: {analysis.category.value}", f"summary: {analysis.summary}"]
    if analysis.regressions:
        lines.append(f"regressions (passed before your patch, fail now): {analysis.regressions}")
    if analysis.still_failing_targets:
        lines.append(f"target tests still failing: {analysis.still_failing_targets}")
    if run is not None:
        for f in run.failures[:4]:
            lines.append(f"--- {f.nodeid} ({f.kind}): {f.message}\n{f.excerpt[-1200:]}")
        if not run.failures and run.output_tail:
            lines.append(run.output_tail[-1500:])
    return neutralize("\n".join(lines))[:FEEDBACK_MAX_CHARS]


ESCALATION = {"implement": "plan", "plan": "explore", "explore": "abort"}


ANALYZE_DIFF_CHARS = 4000


@node("analyze_failure")
async def analyze_failure(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    """Decide what to do with a failed test run. Order matters: cheap deterministic stops first,
    the LLM only when the route is still open.

    1. rule-based abort (missing dependency, sandbox error, invalid selection)
    2. iteration budget
    3. oscillation: the failing patch is identical to an earlier accepted patch
    4. stagnation: the same failure signature seen before -> escalate one level
       (implement -> plan -> explore -> abort); a third occurrence aborts
    5. LLM root cause (test failures / regressions routed to plan): may send the run back to
       explore when the patch changed code that is not on the failing path
    6. explore = roll the workspace back to the base commit (keeping the reproduction test)
    """
    analysis = state.get("failure_analysis")
    assert analysis is not None
    history = state.get("failure_history", [])
    runs = state.get("test_runs", [])
    target_run = next((r for r in reversed(runs) if r.kind is TestRunKind.TARGET), None)
    update: dict[str, Any] = {"validation_attempts": 0}

    def stop(kind: str, msg: str, reason: TerminationReason) -> dict[str, Any]:
        return {
            **update,
            "failure_history": [analysis],
            **_fail("analyze_failure", kind, msg, reason),
        }

    if analysis.route == "abort":
        reason = (
            TerminationReason.ENV_SETUP
            if analysis.category is FailureCategory.MISSING_DEPENDENCY
            else TerminationReason.SANDBOX_ERROR
        )
        return stop(analysis.category.value, analysis.summary, reason)
    b = ctx.meter.budget
    if b.used_iterations >= b.max_iterations:
        return stop("iterations_exhausted",
                    f"{b.used_iterations} iterations used; last failure: {analysis.summary}",
                    TerminationReason.ITERATIONS_EXHAUSTED)  # fmt: skip
    if ctx.settings.agent.failure_routing == "generic":
        # Ablation: no classifier-driven routing, escalation or analysis. Retry implement with
        # the raw test output until the iteration budget runs out.
        raw = failure_feedback(analysis, target_run)
        final = analysis.model_copy(update={"route": "implement"})
        return {**update, "failure_history": [final], "failure_analysis": final,
                "failure_feedback": raw, "validation_feedback": None}  # fmt: skip
    accepted = [p for p in state.get("patch_history", []) if p.validation.accepted]
    if len(accepted) >= 2 and accepted[-1].diff_sha256 in {p.diff_sha256 for p in accepted[:-1]}:
        return stop("oscillation", "the failing patch is identical to an earlier attempt",
                    TerminationReason.OSCILLATION)  # fmt: skip

    seen = sum(1 for a in history if a.signature == analysis.signature)
    route: str = analysis.route
    if seen >= 2:
        return stop(FailureCategory.REPEATED_FAILURE.value,
                    f"same failure a third time ({analysis.category.value}): {analysis.summary}",
                    TerminationReason.REPEATED_FAILURE)  # fmt: skip
    if seen == 1:
        route = ESCALATION[route]
        ctx.recorder.record("recovery", action="escalate", category=analysis.category.value,
                            from_route=analysis.route, to_route=route)  # fmt: skip
        if route == "abort":
            return stop(FailureCategory.REPEATED_FAILURE.value,
                        f"same failure after re-exploring: {analysis.summary}",
                        TerminationReason.REPEATED_FAILURE)  # fmt: skip

    feedback = failure_feedback(analysis, target_run)
    ws, _ = ctx.require_workspace()
    if (
        route == "plan"
        and ctx.settings.agent.llm_failure_analysis
        and analysis.category in (FailureCategory.TEST_FAILURE, FailureCategory.REGRESSION)
    ):
        try:
            rc = await ctx.gateway.structured(
                ModelRole.REASONING,
                [
                    Message(role="system", content=T.ANALYZE_SYSTEM),
                    Message(role="user", content=T.analyze_task(
                        state["issue"], state["plan"], ws.diff()[:ANALYZE_DIFF_CHARS], feedback)),
                ],
                RootCauseAnalysis,
                purpose="analyze_failure",
            )  # fmt: skip
        except ModelError as exc:  # analysis is advisory: fall back to the rule-based route
            ctx.recorder.record("recovery", action="analysis_unavailable", error=str(exc)[:300])
        else:
            update["root_causes"] = [rc]
            feedback += neutralize(
                f"\nroot cause (analysis): {rc.root_cause}\n"
                f"revised hypothesis: {rc.revised_hypothesis}"
            )[:FEEDBACK_MAX_CHARS]
            if not rc.fix_in_right_place:
                route = "explore"
                ctx.recorder.record("recovery", action="reroute", to_route="explore",
                                    reason="analysis: fix not on the failing path")  # fmt: skip

    if route == "explore":
        repro = state.get("repro")
        keep = {repro.path: repro.content} if repro is not None else None
        await asyncio.to_thread(ws.reset_to_base, keep)
        ctx.index = SymbolIndex.build(ws.root, ws.ls_files())
        ctx.executor.reset_memory()
        update["rollbacks"] = state.get("rollbacks", 0) + 1
        ctx.recorder.record("recovery", action="rollback", to_commit=ws.base_commit,
                            kept=list(keep or {}))  # fmt: skip

    final = analysis.model_copy(update={"route": route})
    return {**update, "failure_history": [final], "failure_analysis": final,
            "failure_feedback": feedback, "validation_feedback": None}  # fmt: skip
