"""Verification levels computed purely from stored test runs (never from model output).

L0 nothing verified
L1 changed Python files parse and changed modules import inside the sandbox
L2 L1 + at least one test that failed before the patch passes after it (fail -> pass evidence)
L3 L2 + no regressions: every test that passed at baseline still passes
L4 L3 + the full suite passes at least as many tests as the baseline + the patch introduces no
   new lint findings (ruff: syntax errors, undefined names, unused imports/variables)
L5 L4 + task-specific acceptance tests pass (user-supplied, never shown to the agent; the
   benchmark scores its held-out tests the same way, outside the graph)

VERIFIED means L3 or higher. Levels are cumulative: without fail->pass evidence a patch cannot be
VERIFIED, however many existing tests still pass. The reproduce step supplies that evidence for
bugs no existing test covers.
"""

from __future__ import annotations

from swe_agent.schemas.state import (
    BaselineResult,
    TestRun,
    TestRunStatus,
    VerificationResult,
    VerificationStatus,
)
from swe_agent.verification.classifier import compare_to_baseline


def compute_verification(
    *,
    static_ok: bool,
    import_check: TestRun | None,
    target: TestRun | None,
    baseline: BaselineResult | None,
    targets: list[str],
    lint: TestRun | None = None,
    new_lint: list[str] | None = None,
    acceptance: TestRun | None = None,
) -> VerificationResult:
    evidence: list[str] = []
    notes: list[str] = []
    level = 0

    imports_ok = import_check is None or import_check.status is TestRunStatus.PASSED
    if static_ok and imports_ok:
        level = 1
        evidence.append("changed Python files parse (ast)")
        if import_check is not None:
            evidence.append(
                f"changed modules import in sandbox: {import_check.passed} (run {import_check.id})"
            )
    elif import_check is not None and not imports_ok:
        notes.append(f"import check failed: {import_check.errors}")

    if level >= 1 and target is not None and baseline is not None:
        regressions, still, fixed = compare_to_baseline(target, baseline, targets)
        collection_ok = not (set(target.collection_errors) - set(baseline.collection_errors))
        if fixed and not still:
            level = 2
            evidence.append(f"fail -> pass: {fixed} (baseline {baseline.run_id} -> {target.id})")
            if not regressions and collection_ok:
                level = 3
                evidence.append(
                    f"no regressions: {len(baseline.passing)} baseline-passing tests still pass "
                    f"({len(target.passed)} passing now)"
                )
        else:
            notes.append(
                "no test demonstrates the fix: no test that failed before the patch passes after "
                "it (no existing test covers the bug and no reproduction test was accepted)"
                if not fixed
                else f"target tests still failing: {still}"
            )
        if regressions:
            notes.append(f"regressions: {regressions}")
        elif level < 3 and collection_ok:
            notes.append(
                f"existing suite: no regressions ({len(target.passed)} passed, "
                f"{len(target.not_passing)} not passing, baseline failing {len(baseline.failing)})"
            )
    if level >= 3 and target is not None and baseline is not None:
        if lint is None:
            notes.append("L4 not checked: lint disabled")
        elif lint.status is TestRunStatus.INFRA_ERROR:
            notes.append("L4 not reached: lint could not run in the sandbox (is ruff installed?)")
        elif new_lint:
            notes.append(f"L4 not reached: the patch introduces lint findings: {new_lint[:5]}")
        elif len(target.passed) < len(baseline.passing):
            notes.append("L4 not reached: fewer tests pass than at baseline")
        else:
            level = 4
            evidence.append(
                f"full suite >= baseline ({len(target.passed)} >= {len(baseline.passing)} passing)"
                f"; no new lint findings in changed files (run {lint.id})"
            )
    if level >= 4 and acceptance is not None:
        if acceptance.status is TestRunStatus.PASSED and acceptance.passed:
            level = 5
            evidence.append(
                f"acceptance tests pass: {len(acceptance.passed)} (run {acceptance.id})"
            )
        else:
            notes.append(
                f"L5 not reached: acceptance tests {acceptance.status.value}: "
                f"{sorted(acceptance.not_passing)[:5]}"
            )
    status = VerificationStatus.VERIFIED if level >= 3 else VerificationStatus.NOT_VERIFIED
    return VerificationResult(status=status, level=level, evidence=evidence, notes=notes)
