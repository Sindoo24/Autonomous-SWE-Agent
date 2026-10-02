"""Execution nodes: `baseline_tests` (the suite on unpatched code) and `run_tests` (import check,
then the full suite on a disposable snapshot of the patched workspace).

All code execution goes through `SandboxService` (Docker, no network, resource limits)."""

from __future__ import annotations

import asyncio
import re
from pathlib import PurePosixPath
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import _fail, node
from swe_agent.agents.state import AgentState
from swe_agent.repository.pathjail import PROTECTED_FILENAMES, is_test_path
from swe_agent.sandbox.docker import EnvSetupError, SandboxError
from swe_agent.schemas.state import (
    AgentError,
    BaselineResult,
    FailureAnalysis,
    FailureCategory,
    TerminationReason,
    TestRun,
    TestRunKind,
    TestRunStatus,
)
from swe_agent.verification.classifier import classify
from swe_agent.verification.patch_validator import parse_diff

_MODULE_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def changed_modules(files: list[str]) -> list[str]:
    """Importable module names for changed non-test Python files (src/ layout aware)."""
    mods: list[str] = []
    for rel in files:
        if (
            not rel.endswith(".py")
            or is_test_path(rel)
            or PurePosixPath(rel).name in PROTECTED_FILENAMES
        ):
            continue
        parts = list(PurePosixPath(rel).with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(_MODULE_PART.match(p) for p in parts):
            mods.append(".".join(parts))
    return sorted(set(mods))


def _matches(nodeid: str, selector: str) -> bool:
    sel = selector.strip().rstrip("/")
    if not sel:
        return False
    if nodeid == sel or nodeid.startswith(sel + "::"):
        return True
    file = nodeid.split("::", 1)[0]
    return file == sel or file.startswith(sel + "/")


def select_targets(baseline_failing: list[str], tests_to_run: list[str], issue: str) -> list[str]:
    """Baseline-failing tests the fix must make pass: those the plan names (node id, file or
    directory) or whose test function / file the issue mentions. Unrelated pre-existing failures
    are not targets, so they cannot trap the agent in a loop."""
    targets = set()
    for t in baseline_failing:
        if any(_matches(t, sel) for sel in tests_to_run):
            targets.add(t)
        name = t.split("::")[-1].split("[")[0]
        file = t.split("::")[0]
        if re.search(rf"\b{re.escape(name)}\b", issue) or file in issue:
            targets.add(t)
    return sorted(targets)


def project_packages(ws_files: list[str]) -> set[str]:
    pk = set()
    for f in ws_files:
        parts = PurePosixPath(f).parts
        if parts and parts[0] == "src" and len(parts) > 1:
            parts = parts[1:]
        if len(parts) > 1:
            pk.add(parts[0])
        elif f.endswith(".py"):
            pk.add(PurePosixPath(f).stem)
    return pk


def _require_sandbox(ctx: RunContext) -> Any:
    if ctx.sandbox is None:
        raise SandboxError("sandbox disabled")
    return ctx.sandbox


@node("baseline_tests")
async def baseline_tests(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    sandbox = _require_sandbox(ctx)
    try:
        image, spec = await asyncio.to_thread(sandbox.prepare_image, ws)
    except EnvSetupError as exc:
        return _fail("baseline_tests", "env_setup", str(exc), TerminationReason.ENV_SETUP)
    except SandboxError as exc:
        return _fail("baseline_tests", "sandbox_error", str(exc), TerminationReason.SANDBOX_ERROR)
    try:
        run: TestRun = await asyncio.to_thread(sandbox.run_tests, ws, image, TestRunKind.BASELINE)
    except SandboxError as exc:
        return _fail("baseline_tests", "sandbox_error", str(exc), TerminationReason.SANDBOX_ERROR)

    if run.status in (
        TestRunStatus.INFRA_ERROR,
        TestRunStatus.TIMEOUT,
        TestRunStatus.RESOURCE_LIMIT,
        TestRunStatus.USAGE_ERROR,
    ):
        return {
            **_fail(
                "baseline_tests",
                "env_setup",
                f"baseline test run unusable ({run.status.value}): {run.output_tail[-800:]}",
                TerminationReason.ENV_SETUP,
            ),
            "test_runs": [run],
        }
    baseline = BaselineResult(
        image=image,
        run_id=run.id,
        passing=sorted(run.passed),
        failing=sorted(run.not_passing),
        collection_errors=sorted(run.collection_errors),
        status=run.status,
    )
    update: dict[str, Any] = {"sandbox_image": image, "baseline": baseline, "test_runs": [run]}
    if spec.unsupported:
        update["errors"] = [
            AgentError(node="baseline_tests", kind="dependency_warning", message=m)
            for m in spec.unsupported[:10]
        ]
    return update


@node("run_tests")
async def run_tests(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    sandbox = _require_sandbox(ctx)
    image = state.get("sandbox_image")
    baseline = state.get("baseline")
    if not image or baseline is None:
        return _fail(
            "run_tests",
            "sandbox_error",
            "no sandbox image / baseline",
            TerminationReason.SANDBOX_ERROR,
        )

    # Defence in depth: the edit tools and the validator already forbid this.
    files = [p for _, _, p in ws.numstat()]
    tampered = [f for f in files if is_test_path(f) and ws.exists_at_base(f)]
    if tampered:
        return _fail(
            "run_tests",
            "policy_violation",
            f"refusing to run: existing test files modified: {tampered}",
            TerminationReason.INVALID_PATCH,
        )

    repro = state.get("repro")
    if repro is not None:
        on_disk = ws.jail.resolve(repro.path)
        if not on_disk.is_file() or on_disk.read_text(encoding="utf-8") != repro.content:
            return _fail(
                "run_tests",
                "policy_violation",
                f"refusing to run: the reproduction test {repro.path} was modified",
                TerminationReason.INVALID_PATCH,
            )

    plan = state["plan"]
    targets = select_targets(baseline.failing, plan.tests_to_run, state["issue"])
    if repro is not None and repro.nodeid not in targets:
        targets = sorted([*targets, repro.nodeid])
    runs: list[TestRun] = []
    try:
        mods = changed_modules(files)
        if mods:
            imp = await asyncio.to_thread(sandbox.import_check, ws, image, mods)
            runs.append(imp)
        run = await asyncio.to_thread(sandbox.run_tests, ws, image, TestRunKind.TARGET)
        runs.append(run)
    except SandboxError as exc:
        return {
            **_fail("run_tests", "sandbox_error", str(exc), TerminationReason.SANDBOX_ERROR),
            "test_runs": runs,
        }

    added = "\n".join(text for _, text in parse_diff(ws.diff()).added_lines)
    analysis = classify(run, baseline, targets, project_packages(ws.ls_files()), added)
    if (
        analysis is None
        and runs[0].kind is TestRunKind.IMPORT_CHECK
        and (runs[0].status is not TestRunStatus.PASSED)
    ):
        analysis = FailureAnalysis(
            category=FailureCategory.IMPORT_ERROR,
            signature=f"import:{','.join(runs[0].errors)}",
            route="implement",
            summary=f"changed modules fail to import: {runs[0].errors}",
            details=[f.excerpt[-300:] for f in runs[0].failures[:3]],
        )
    return {"test_runs": runs, "targets": targets, "failure_analysis": analysis}
