"""implement <-> validate_patch inner loop."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.loop import run_tool_loop
from swe_agent.agents.nodes.common import node
from swe_agent.agents.state import AgentState
from swe_agent.llm.gateway import ModelRole
from swe_agent.prompts import templates as T
from swe_agent.repository.pathjail import WritePolicy
from swe_agent.schemas.state import ImplementationSummary, PatchIteration
from swe_agent.tools.base import ToolContext
from swe_agent.verification.patch_validator import validate_patch as run_validator

# implement may change source files only: tests, CI and build files are read-only.
IMPLEMENT_POLICY = WritePolicy(allow_source=True, allow_new_tests=False, allow_existing_tests=False)


@node("implement")
async def implement(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, index = ctx.require_workspace()
    plan = state["plan"]
    feedback = state.get("validation_feedback")
    if not feedback:
        # A new plan version starts a new patch iteration; validator retries do not.
        ctx.meter.add_iteration()
        ctx.recorder.current_iteration = ctx.meter.budget.used_iterations
    tool_ctx = ToolContext(
        workspace=ws,
        index=index,
        node="implement",
        write_policy=IMPLEMENT_POLICY,
        edits=ctx.edits,
    )
    summary = await run_tool_loop(
        ctx,
        node="implement",
        role=ModelRole.CODER,
        system=T.IMPLEMENT_SYSTEM,
        task=T.implement_task(
            state["issue"],
            plan,
            feedback,
            state.get("failure_feedback"),
            repro.nodeid if (repro := state.get("repro")) else None,
        ),
        finish_model=ImplementationSummary,
        finish_description="Report what you changed and the root cause you fixed.",
        max_steps=ctx.settings.budget.implement_max_steps,
        tool_ctx=tool_ctx,
    )
    return {"implementation": summary}


@node("validate_patch")
async def validate_patch(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    plan = state["plan"]
    diff = ws.diff()
    repro = state.get("repro")
    policy = (
        replace(IMPLEMENT_POLICY, allowed_new_tests=frozenset({repro.path}))
        if repro is not None
        else IMPLEMENT_POLICY
    )
    validation, deviation, stats = run_validator(
        ws,
        diff,
        plan,
        policy,
        max_changed_lines=ctx.settings.max_patch_changed_lines,
        repro_path=repro.path if repro is not None else None,
    )
    ref = ctx.artifacts.put_text("diff", diff, suffix=".patch")
    attempts = state.get("validation_attempts", 0) + 1
    iteration = PatchIteration(
        iteration=max(ctx.meter.budget.used_iterations, 1),
        attempt=attempts,
        plan_version=plan.version,
        diff_artifact_id=ref.id,
        diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        files_changed=stats.files,
        lines_added=stats.added,
        lines_removed=stats.removed,
        deviation=deviation,
        validation=validation,
    )
    ctx.recorder.record(
        "tool_call",  # recorded as a deterministic system tool for the trace
        tool="patch_validator",
        system=True,
        args={},
        status="ok" if validation.accepted else "rejected",
        duration_ms=0,
        errors=validation.errors,
        warnings=validation.warnings,
    )
    update: dict[str, Any] = {"patch_history": [iteration], "validation_attempts": attempts}
    if validation.accepted:
        update["validation_feedback"] = None
        update["final_diff_artifact_id"] = ref.id
    else:
        update["validation_feedback"] = "\n".join(f"- {e}" for e in validation.errors)
    return update
