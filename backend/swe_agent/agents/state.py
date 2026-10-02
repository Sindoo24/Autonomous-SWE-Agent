"""LangGraph state definition.

Every field has one writer node. History fields are append-only via `operator.add` reducers.
Raw prompts, model responses and tool outputs are NOT in state: they are in the trajectory and
the artifact store, referenced by id.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from swe_agent.schemas.state import (
    AgentError,
    ApprovalDecision,
    BaselineResult,
    Budget,
    FailureAnalysis,
    Findings,
    HypothesisSet,
    ImplementationSummary,
    PatchIteration,
    Plan,
    RepoRef,
    RepoSummary,
    ReproResult,
    RootCauseAnalysis,
    TerminationReason,
    TestRun,
    VerificationResult,
)


class AgentState(TypedDict, total=False):
    # identity / input
    task_id: str
    run_id: str
    repo: RepoRef
    issue: str
    # understanding
    repo_summary: RepoSummary
    findings: Findings
    hypotheses: HypothesisSet
    # planning & patching
    plan: Plan
    plan_history: Annotated[list[Plan], operator.add]
    implementation: ImplementationSummary
    patch_history: Annotated[list[PatchIteration], operator.add]
    validation_feedback: str | None
    validation_attempts: int
    # execution
    sandbox_image: str | None
    baseline: BaselineResult | None
    targets: list[str]  # baseline-failing tests the fix must make pass
    test_runs: Annotated[list[TestRun], operator.add]
    failure_analysis: FailureAnalysis | None
    failure_history: Annotated[list[FailureAnalysis], operator.add]
    failure_feedback: str | None  # test-failure evidence handed to plan / implement
    # recovery
    repro: ReproResult | None  # agent-written failing test (None: skipped or not reproduced)
    root_causes: Annotated[list[RootCauseAnalysis], operator.add]
    rollbacks: int  # times the workspace was reset to base before re-exploring
    # approval
    approval: ApprovalDecision | None
    # control
    current_node: str
    budget: Budget
    errors: Annotated[list[AgentError], operator.add]
    termination_reason: TerminationReason | None
    # outcome
    final_diff_artifact_id: str | None
    verification: VerificationResult
    report_artifact_id: str
