"""Typed models that make up the agent state.

Rules:
- Graph state holds small, typed values. Large blobs (diffs, prompts, logs) live in the
  artifact store and are referenced by id.
- LLM-produced models (Findings, HypothesisSet, Plan, ...) are validated with Pydantic and then
  re-validated semantically against the workspace (files exist, line ranges valid).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- repository


class RepoRef(Strict):
    source: str = Field(description="URL or local path the user supplied")
    workspace_path: str = ""
    branch: str = ""
    base_commit: str = ""


class CandidateFile(Strict):
    path: str
    score: float
    reasons: list[str]


class RepoSummary(Strict):
    file_count: int
    python_file_count: int
    top_level: list[str]
    packages: list[str]
    test_dirs: list[str]
    test_framework: Literal["pytest", "unittest", "unknown"]
    manifests: list[str]
    entry_points: list[str]
    candidates: list[CandidateFile]
    issue_terms: list[str]


# --------------------------------------------------------------------------- understanding


class EvidenceRef(Strict):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    why: str


class Findings(Strict):
    """Output of the explore node."""

    summary: str = Field(description="What the relevant code does and where the bug likely is")
    relevant_files: list[str] = Field(description="Repository-relative paths, most relevant first")
    relevant_symbols: list[str] = Field(description="Function/class names involved")
    evidence: list[EvidenceRef]


class Hypothesis(Strict):
    id: str
    statement: str
    evidence: list[EvidenceRef]
    confidence: Literal["low", "medium", "high"]
    status: Literal["open", "confirmed", "refuted"] = "open"


class HypothesisSet(Strict):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)


class PlannedChange(Strict):
    file: str
    kind: Literal["modify", "create"] = "modify"
    symbol: str | None = None
    description: str


class ReproSpec(Strict):
    test_path: str
    test_name: str
    description: str


class Plan(Strict):
    version: int = 1
    problem: str
    hypothesis_id: str
    files_to_modify: list[str] = Field(min_length=1)
    changes: list[PlannedChange] = Field(min_length=1)
    reproduction: ReproSpec | None = None
    tests_to_run: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    rollback_strategy: str


class ImplementationSummary(Strict):
    """What the implement node's model says it did. Never trusted on its own: the diff is."""

    summary: str
    root_cause: str


# --------------------------------------------------------------------------- patches


class DeviationSeverity(StrEnum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"


class PlanDeviation(Strict):
    severity: DeviationSeverity
    files_outside_plan: list[str]
    planned_files_untouched: list[str]
    reasons: list[str]


class PatchValidation(Strict):
    accepted: bool
    errors: list[str]
    warnings: list[str]


class PatchIteration(Strict):
    iteration: int
    attempt: int
    plan_version: int
    diff_artifact_id: str
    diff_sha256: str
    files_changed: list[str]
    lines_added: int
    lines_removed: int
    deviation: PlanDeviation
    validation: PatchValidation


# --------------------------------------------------------------------------- control


class Budget(Strict):
    max_iterations: int
    max_tool_calls: int
    max_tokens: int
    max_wall_seconds: int
    used_iterations: int = 0
    used_tool_calls: int = 0
    used_tokens: int = 0
    elapsed_seconds: float = 0.0


class AgentError(Strict):
    node: str
    kind: str
    message: str


class TerminationReason(StrEnum):
    PATCH_PROPOSED = "patch_proposed"  # patch produced and tested; see verification for level
    BUDGET_EXHAUSTED = "budget_exhausted"
    ITERATIONS_EXHAUSTED = "iterations_exhausted"  # tests still failing after max iterations
    REPEATED_FAILURE = "repeated_failure"  # same failure signature twice in a row
    OSCILLATION = "oscillation"  # a failing patch identical to an earlier one came back
    REJECTED_BY_HUMAN = "rejected_by_human"
    ENV_SETUP = "env_setup"  # sandbox image / dependency installation / baseline collection
    SANDBOX_ERROR = "sandbox_error"  # docker unavailable or infrastructure failure
    INVALID_PATCH = "invalid_patch"
    NO_CHANGES = "no_changes"
    MODEL_ERROR = "model_error"
    REPO_ERROR = "repo_error"
    INVALID_INPUT = "invalid_input"
    CANCELLED = "cancelled"  # cancel requested through the API (checked between nodes)


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT VERIFIED"


class VerificationResult(Strict):
    status: VerificationStatus
    level: int = Field(ge=0, le=5)
    evidence: list[str]
    notes: list[str]


# --------------------------------------------------------------------------- execution


class TestRunKind(StrEnum):
    BASELINE = "baseline"  # unpatched code, full suite
    TARGET = "target"  # patched code, full suite
    IMPORT_CHECK = "import_check"  # patched code, import every changed module
    REPRO = "repro"  # unpatched code, the agent-written reproduction test only
    LINT = "lint"  # ruff on changed files, before vs after
    ACCEPTANCE = "acceptance"  # user-supplied acceptance tests the agent never sees (L5)
    HELDOUT = "heldout"  # benchmark only: tests the agent never sees


class TestRunStatus(StrEnum):
    PASSED = "passed"  # pytest exit 0
    FAILED = "failed"  # tests ran, some failed/errored
    COLLECTION_ERROR = "collection_error"  # pytest could not import/collect (exit 2)
    NO_TESTS = "no_tests"  # exit 5
    USAGE_ERROR = "usage_error"  # exit 4: bad selection / arguments
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"  # OOM-killed
    INFRA_ERROR = "infra_error"  # docker failure, internal pytest error (exit 3)


class TestCaseFailure(Strict):
    nodeid: str
    kind: Literal["failure", "error"]
    message: str
    excerpt: str


class TestRun(Strict):
    __test__ = False  # not a pytest test class

    id: str
    kind: TestRunKind
    status: TestRunStatus
    command: list[str]
    exit_code: int | None
    passed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)  # assertion failures
    errors: list[str] = Field(default_factory=list)  # exceptions in tests / setup
    skipped: int = 0
    collection_errors: list[str] = Field(default_factory=list)
    failures: list[TestCaseFailure] = Field(default_factory=list)  # first few, for feedback
    duration_ms: int
    timed_out: bool = False
    oom_killed: bool = False
    output_tail: str = ""  # last part of combined stdout/stderr
    log_artifact_id: str | None = None
    junit_artifact_id: str | None = None

    @property
    def not_passing(self) -> set[str]:
        return set(self.failed) | set(self.errors)


class BaselineResult(Strict):
    image: str
    run_id: str
    passing: list[str]
    failing: list[str]
    collection_errors: list[str]
    status: TestRunStatus


class FailureCategory(StrEnum):
    SYNTAX_ERROR = "syntax_error"
    IMPORT_ERROR = "import_error"
    MISSING_DEPENDENCY = "missing_dependency"
    TEST_FAILURE = "test_failure"
    REGRESSION = "regression"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    INVALID_SELECTION = "invalid_selection"
    SANDBOX_ERROR = "sandbox_error"
    REPEATED_FAILURE = "repeated_failure"


class FailureAnalysis(Strict):
    category: FailureCategory
    signature: str
    route: Literal["implement", "plan", "explore", "abort"]
    summary: str
    regressions: list[str] = Field(default_factory=list)
    still_failing_targets: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- recovery


class ReproDraft(Strict):
    """What the model proposes as a reproduction test (whole file content)."""

    test_name: str = Field(description="Name of the test function, must start with test_")
    code: str = Field(description="Complete content of the new test file")
    expected_failure: str = Field(description="How the test fails on the current buggy code")


class ReproResult(Strict):
    path: str
    test_name: str
    nodeid: str
    failing_run_id: str
    content: str
    attempts: int


class RootCauseAnalysis(Strict):
    """LLM analysis of a failed test run. Routing stays deterministic: the model can
    only choose between re-planning in place or re-exploring from a clean workspace."""

    root_cause: str = Field(description="Why the patch did not fix the failing tests")
    fix_in_right_place: bool = Field(
        description="True if the bug is in the code the patch changed; false if the real cause "
        "is elsewhere"
    )
    revised_hypothesis: str


# --------------------------------------------------------------------------- approval


class ApprovalDecision(Strict):
    approved: bool
    feedback: str | None = None
    retry: bool = False  # on rejection: re-plan with the feedback instead of stopping
    decided_by: str = "human"
