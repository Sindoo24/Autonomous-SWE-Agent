"""Rule-based failure classification (deterministic; an optional LLM
root-cause analysis runs on top of it in `analyze_failure`).

Input: the target test run on the patched code, the baseline, and the repo's own top-level
package names (to tell a broken project import from a missing third-party dependency).
Output: a category, a stable signature for stagnation detection, and a route.
"""

from __future__ import annotations

import hashlib
import re

from swe_agent.schemas.state import (
    BaselineResult,
    FailureAnalysis,
    FailureCategory,
    TestRun,
    TestRunStatus,
)

_MODULE_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")
_IMPORT_ERROR = re.compile(r"\bImportError\b|ModuleNotFoundError")
_SYNTAX = re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")
_FRAME = re.compile(r'File "([^"]+)", line \d+|^([\w/.-]+\.py):\d+:', re.MULTILINE)

ROUTES: dict[FailureCategory, str] = {
    FailureCategory.SYNTAX_ERROR: "implement",
    FailureCategory.IMPORT_ERROR: "implement",
    FailureCategory.TEST_FAILURE: "plan",
    FailureCategory.REGRESSION: "plan",
    FailureCategory.TIMEOUT: "plan",
    FailureCategory.RESOURCE_LIMIT: "plan",
    FailureCategory.MISSING_DEPENDENCY: "abort",
    FailureCategory.INVALID_SELECTION: "abort",
    FailureCategory.SANDBOX_ERROR: "abort",
    FailureCategory.REPEATED_FAILURE: "abort",
}


def _text(run: TestRun) -> str:
    return "\n".join([run.output_tail, *(f.excerpt for f in run.failures)])


def _signature(category: FailureCategory, tests: list[str], text: str) -> str:
    frames = [a or b for a, b in _FRAME.findall(text)][-1:]
    raw = "|".join([category.value, *sorted(tests)[:20], *frames])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compare_to_baseline(
    run: TestRun, baseline: BaselineResult, targets: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return (regressions, still-failing targets, fixed tests)."""
    failing_now = run.not_passing
    regressions = sorted(t for t in baseline.passing if t in failing_now)
    still = sorted(t for t in targets if t not in run.passed)
    fixed = sorted(t for t in baseline.failing if t in run.passed)
    return regressions, still, fixed


def classify(
    run: TestRun,
    baseline: BaselineResult,
    targets: list[str],
    project_packages: set[str],
    patch_added_text: str = "",
) -> FailureAnalysis | None:
    """None means the run counts as a pass (no regressions, targets pass, nothing broken)."""
    regressions, still, _ = compare_to_baseline(run, baseline, targets)
    text = _text(run)
    details = [f"{f.nodeid}: {f.message}"[:300] for f in run.failures[:5]]

    def result(cat: FailureCategory, summary: str, tests: list[str]) -> FailureAnalysis:
        return FailureAnalysis(
            category=cat,
            signature=_signature(cat, tests, text),
            route=ROUTES[cat],
            summary=summary,
            regressions=regressions,
            still_failing_targets=still,
            details=details,
        )

    if run.status is TestRunStatus.TIMEOUT:
        return result(
            FailureCategory.TIMEOUT,
            "test run exceeded the time limit "
            "(possible infinite loop or deadlock introduced by the patch)",
            [],
        )
    if run.status is TestRunStatus.RESOURCE_LIMIT:
        return result(
            FailureCategory.RESOURCE_LIMIT,
            "test run was killed for exceeding memory/process limits",
            [],
        )
    if run.status in (TestRunStatus.USAGE_ERROR,):
        return result(FailureCategory.INVALID_SELECTION, "pytest rejected the test selection", [])
    if run.status is TestRunStatus.INFRA_ERROR:
        return result(FailureCategory.SANDBOX_ERROR, "sandbox/pytest infrastructure error", [])

    new_collection = sorted(set(run.collection_errors) - set(baseline.collection_errors))
    if run.status is TestRunStatus.COLLECTION_ERROR or new_collection:
        if _SYNTAX.search(text):
            return result(
                FailureCategory.SYNTAX_ERROR, "syntax error while importing tests", new_collection
            )
        missing = _MODULE_NOT_FOUND.findall(text)
        if missing:
            top = {m.split(".")[0] for m in missing}
            # An import the patch itself added is the patch's bug, not the environment's.
            introduced = {
                m for m in top if re.search(rf"\bimport\s+{m}\b|\bfrom\s+{m}\b", patch_added_text)
            }
            if introduced:
                return result(
                    FailureCategory.IMPORT_ERROR,
                    f"the patch imports module(s) that do not exist: {sorted(introduced)}",
                    new_collection,
                )
            external = sorted(top - project_packages)
            if external:
                return result(
                    FailureCategory.MISSING_DEPENDENCY,
                    f"third-party module(s) not installed in the sandbox: {external}",
                    new_collection,
                )
        if _IMPORT_ERROR.search(text):
            return result(
                FailureCategory.IMPORT_ERROR, "import error while collecting tests", new_collection
            )
        return result(FailureCategory.IMPORT_ERROR, "tests could not be collected", new_collection)

    if regressions:
        return result(
            FailureCategory.REGRESSION,
            f"{len(regressions)} test(s) that passed before the patch now fail",
            regressions,
        )
    if still:
        return result(
            FailureCategory.TEST_FAILURE,
            f"{len(still)} target test(s) still failing",
            still,
        )
    return None


def repeated(analysis: FailureAnalysis, previous: list[str]) -> bool:
    return bool(previous) and previous[-1] == analysis.signature
