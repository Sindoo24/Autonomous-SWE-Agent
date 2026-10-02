"""Human approval before anything leaves the isolated workspace.

verify -> human_approval -> finalize                 (approved)
                         -> plan                     (rejected with feedback + retry)
                         -> report_failure           (rejected)

In "manual" mode the node calls LangGraph's `interrupt()`: the run is checkpointed to SQLite and
the process can exit. `swe-agent approve|reject <run_id>` resumes it later, from any process.
The request (verdict, evidence, diff) is also written to `approval_request.json` and
`pending.patch` in the run's artifact directory for review.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.types import interrupt

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node
from swe_agent.agents.state import AgentState
from swe_agent.prompts.safety import neutralize
from swe_agent.schemas.state import AgentError, ApprovalDecision, TerminationReason

FEEDBACK_MAX_CHARS = 4000


def approval_request(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    history = state.get("patch_history", [])
    last = history[-1] if history else None
    ver = state.get("verification")
    impl = state.get("implementation")
    plan = state.get("plan")
    repro = state.get("repro")
    return {
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "issue": state.get("issue"),
        "verification": ver.model_dump(mode="json") if ver else None,
        "root_cause": impl.root_cause if impl else None,
        "explanation": impl.summary if impl else None,
        "files_changed": last.files_changed if last else [],
        "lines": {"added": last.lines_added, "removed": last.lines_removed} if last else None,
        "reproduction_test": repro.nodeid if repro else None,
        "risks": plan.risks if plan else [],
        "iterations": ctx.meter.budget.used_iterations,
        "failures_along_the_way": [
            f"{a.category.value}: {a.summary}" for a in state.get("failure_history", [])
        ],
        "patch_file": str(ctx.artifacts.root / "pending.patch"),
        "how_to_decide": (
            f"swe-agent approve {ctx.run_id}   |   "
            f"swe-agent reject {ctx.run_id} [--feedback TEXT --retry]"
        ),
    }


@node("human_approval")
async def human_approval(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    if ctx.settings.agent.approval == "auto":
        decision = ApprovalDecision(approved=True, decided_by="auto")
    else:
        request = approval_request(state, ctx)
        art = state.get("final_diff_artifact_id")
        if art:
            (ctx.artifacts.root / "pending.patch").write_text(
                ctx.artifacts.read_text(art), encoding="utf-8"
            )
        (ctx.artifacts.root / "approval_request.json").write_text(
            json.dumps(request, indent=2), encoding="utf-8"
        )
        ctx.recorder.record("approval", action="requested", run=ctx.run_id)
        # Pauses the graph here; on resume, returns the value passed to Command(resume=...).
        decision = ApprovalDecision.model_validate(interrupt(request))

    ctx.recorder.record("approval", action="decided", **decision.model_dump())
    update: dict[str, Any] = {"approval": decision}
    if decision.approved:
        return update
    b = ctx.meter.budget
    if decision.retry and decision.feedback and b.used_iterations < b.max_iterations:
        update["failure_feedback"] = neutralize(
            f"A human reviewer REJECTED the previous patch. Reviewer feedback: {decision.feedback}"
        )[:FEEDBACK_MAX_CHARS]
        update["validation_feedback"] = None
        update["validation_attempts"] = 0
        return update
    note = "" if not decision.retry else " (retry requested but no feedback or no iterations left)"
    return {
        **update,
        "errors": [
            AgentError(
                node="human_approval",
                kind="rejected",
                message=f"patch rejected by {decision.decided_by}{note}: "
                f"{decision.feedback or '-'}",
            )
        ],
        "termination_reason": TerminationReason.REJECTED_BY_HUMAN,
    }
