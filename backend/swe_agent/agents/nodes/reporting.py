"""finalize / report_failure: produce the run report (JSON) and the patch file.

The verdict comes from the `verify` node (computed from sandbox test runs). With
execution disabled (SANDBOX_ENABLED=false) nothing runs, and the report says so explicitly
(NOT VERIFIED, level 0) rather than implying the patch works.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node
from swe_agent.agents.state import AgentState
from swe_agent.repository.workspace import patch_applies_at_base
from swe_agent.schemas.state import (
    TerminationReason,
    TestRun,
    TestRunKind,
    VerificationResult,
    VerificationStatus,
)

NO_EXECUTION_NOTE = (
    "Execution disabled (SANDBOX_ENABLED=false): the patch was generated and statically "
    "validated (paths, policy, syntax, plan deviation) but NOT executed. No tests were run, so "
    "nothing is verified."
)


def _verification(state: AgentState) -> VerificationResult:
    if "verification" in state:
        return state["verification"]
    evidence: list[str] = []
    history = state.get("patch_history", [])
    if history and history[-1].validation.accepted:
        evidence.append("static validation passed: changed Python files parse (ast)")
    return VerificationResult(
        status=VerificationStatus.NOT_VERIFIED,
        level=0,
        evidence=evidence,
        notes=[NO_EXECUTION_NOTE],
    )


def build_report(
    state: AgentState, ctx: RunContext, verification: VerificationResult
) -> dict[str, Any]:
    history = state.get("patch_history", [])
    last = history[-1] if history else None
    impl = state.get("implementation")
    plan = state.get("plan")
    repo = state.get("repo")
    budget = ctx.meter.snapshot()
    return {
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "status": verification.status.value,
        "verification": verification.model_dump(mode="json"),
        "termination_reason": (
            state.get("termination_reason") or TerminationReason.PATCH_PROPOSED
        ).value,
        "issue": state.get("issue"),
        "repository": repo.model_dump() if repo else None,
        "root_cause": impl.root_cause if impl else None,
        "implementation_explanation": impl.summary if impl else None,
        "hypotheses": state["hypotheses"].model_dump()["hypotheses"]
        if "hypotheses" in state
        else [],
        "plan": plan.model_dump() if plan else None,
        "files_changed": last.files_changed if last else [],
        "diff_artifact": last.diff_artifact_id if last else None,
        "lines_added": last.lines_added if last else 0,
        "lines_removed": last.lines_removed if last else 0,
        "iterations": budget.used_iterations,
        "patch_attempts": [
            {
                "iteration": p.iteration,
                "attempt": p.attempt,
                "plan_version": p.plan_version,
                "accepted": p.validation.accepted,
                "errors": p.validation.errors,
                "warnings": p.validation.warnings,
                "deviation": p.deviation.model_dump(mode="json"),
                "diff_artifact": p.diff_artifact_id,
            }
            for p in history
        ],
        "baseline": _baseline_summary(state),
        "targets": state.get("targets", []),
        "tests_executed": [_run_summary(r) for r in state.get("test_runs", [])],
        "test_results": _final_results(state),
        "failure_history": [a.model_dump(mode="json") for a in state.get("failure_history", [])],
        "reproduction": _repro(state),
        "root_cause_analyses": [r.model_dump() for r in state.get("root_causes", [])],
        "rollbacks": state.get("rollbacks", 0),
        "approval": a.model_dump() if (a := state.get("approval")) else None,
        "errors": [e.model_dump() for e in state.get("errors", [])],
        "budget": budget.model_dump(),
        "trace": ctx.recorder.summary(),
        "latency_s": round(budget.elapsed_seconds, 2),
        "trajectory_file": str(ctx.recorder.path),
    }


def _repro(state: AgentState) -> dict[str, Any] | None:
    r = state.get("repro")
    if r is None:
        return None
    return {"test": r.nodeid, "path": r.path, "failed_before_run": r.failing_run_id,
            "attempts": r.attempts}  # fmt: skip


def _run_summary(r: TestRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind.value,
        "status": r.status.value,
        "command": r.command,
        "exit_code": r.exit_code,
        "passed": len(r.passed),
        "failed": len(r.failed),
        "errors": len(r.errors),
        "collection_errors": r.collection_errors,
        "duration_ms": r.duration_ms,
        "timed_out": r.timed_out,
        "oom_killed": r.oom_killed,
        "log_artifact": r.log_artifact_id,
        "junit_artifact": r.junit_artifact_id,
    }


def _baseline_summary(state: AgentState) -> dict[str, Any] | None:
    b = state.get("baseline")
    if b is None:
        return None
    return {
        "image": b.image,
        "run": b.run_id,
        "status": b.status.value,
        "passing": len(b.passing),
        "failing": b.failing,
        "collection_errors": b.collection_errors,
    }


def _final_results(state: AgentState) -> dict[str, Any] | None:
    runs = [r for r in state.get("test_runs", []) if r.kind is TestRunKind.TARGET]
    if not runs:
        return None
    r = runs[-1]
    return {
        "run": r.id,
        "status": r.status.value,
        "passed": r.passed,
        "failed": r.failed,
        "errors": r.errors,
        "collection_errors": r.collection_errors,
        "failures": [f.model_dump() for f in r.failures],
    }


def _write(ctx: RunContext, report: dict[str, Any], diff_text: str | None) -> dict[str, Any]:
    ref = ctx.artifacts.put_json("report", report)
    out: dict[str, Any] = {"report_artifact_id": ref.id}
    root = Path(ctx.artifacts.root)
    (root / "report.json").write_text(Path(ref.path).read_text(encoding="utf-8"), encoding="utf-8")
    if diff_text:
        (root / "final.patch").write_text(diff_text, encoding="utf-8")
    return out


@node("finalize")
async def finalize(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    verification = _verification(state)
    art = state.get("final_diff_artifact_id")
    diff_text = ctx.artifacts.read_text(art) if art else None
    update = {"verification": verification, "termination_reason": TerminationReason.PATCH_PROPOSED}
    report = build_report({**state, **update}, ctx, verification)  # type: ignore[typeddict-item]
    # Export check: the patch must apply cleanly to a pristine checkout of the base commit.
    if diff_text and ctx.workspace is not None:
        ok, err = await asyncio.to_thread(patch_applies_at_base, ctx.workspace, diff_text)
        report["patch_applies_cleanly"] = ok
        if not ok:
            report["patch_apply_error"] = err
    return {**update, **_write(ctx, report, diff_text)}


@node("report_failure")
async def report_failure(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    history = state.get("patch_history", [])
    reason = state.get("termination_reason")
    update: dict[str, Any] = {}
    if reason is None:
        # Reached via the validator loop running out of attempts.
        no_changes = bool(history) and not history[-1].files_changed
        reason = TerminationReason.NO_CHANGES if no_changes else TerminationReason.INVALID_PATCH
        update["termination_reason"] = reason
    verification = VerificationResult(
        status=VerificationStatus.NOT_VERIFIED,
        level=0,
        evidence=[],
        notes=[f"run terminated: {reason.value}"],
    )
    update["verification"] = verification
    report = build_report({**state, **update}, ctx, verification)  # type: ignore[typeddict-item]
    if (prior := state.get("verification")) is not None:
        report["verification_before_termination"] = prior.model_dump(mode="json")
    best = history[-1].diff_artifact_id if history else None
    report["best_partial_diff_artifact"] = best
    diff_text = ctx.artifacts.read_text(best) if best else None
    return {**update, **_write(ctx, report, diff_text)}
