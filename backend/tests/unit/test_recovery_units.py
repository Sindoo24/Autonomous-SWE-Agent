from __future__ import annotations

import pytest

from swe_agent.agents.nodes.failure_analysis import ESCALATION
from swe_agent.agents.nodes.reproduction import check_draft, judge_repro
from swe_agent.agents.runner import _state_types
from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.pathjail import WritePolicy
from swe_agent.schemas.state import (
    BaselineResult,
    Plan,
    PlannedChange,
    ReproDraft,
    TestCaseFailure,
    TestRun,
    TestRunKind,
    TestRunStatus,
)
from swe_agent.verification.levels import compute_verification
from swe_agent.verification.patch_validator import validate_patch

NODE = "tests/test_swe_agent_repro.py::test_bug"


def run(
    status: TestRunStatus, passed=(), failed=(), errors=(), coll=(), msg="", kind=TestRunKind.REPRO
) -> TestRun:  # type: ignore[no-untyped-def]
    fails = [TestCaseFailure(nodeid=n, kind="failure", message=msg, excerpt=msg) for n in failed]
    return TestRun(id="r", kind=kind, status=status, command=[], exit_code=1, passed=list(passed),
                   failed=list(failed), errors=list(errors), collection_errors=list(coll),
                   failures=fails, duration_ms=1)  # fmt: skip


@pytest.mark.parametrize(
    ("r", "ok", "why"),
    [
        (run(TestRunStatus.FAILED, failed=[NODE], msg="assert 500 == 422"), True, "assert"),
        (run(TestRunStatus.FAILED, failed=[NODE], msg="KeyError: 'email'"), True, "KeyError"),
        (run(TestRunStatus.PASSED, passed=[NODE]), False, "PASSES on the current buggy code"),
        (run(TestRunStatus.FAILED, failed=[NODE], msg="NameError: name 'x' is not defined"),
         False, "wrong reason"),
        (run(TestRunStatus.FAILED, failed=[NODE], msg="ImportError: cannot import name"),
         False, "wrong reason"),
        (run(TestRunStatus.FAILED, coll=["tests/test_swe_agent_repro.py"]), False,
         "could not be collected"),
        (run(TestRunStatus.TIMEOUT), False, "did not complete"),
    ],
)  # fmt: skip
def test_judge_repro(r: TestRun, ok: bool, why: str) -> None:
    got, reason = judge_repro(r, NODE)
    assert got is ok and why in reason


def test_check_draft() -> None:
    good = ReproDraft(test_name="test_bug", code="def test_bug():\n    assert 1 == 2\n",
                      expected_failure="x")  # fmt: skip
    assert check_draft(good) == []
    assert any("syntax error" in e for e in check_draft(good.model_copy(update={"code": "def ("})))
    assert any("top-level function" in e
               for e in check_draft(good.model_copy(update={"test_name": "test_other"})))  # fmt: skip
    assert any("starting with test_" in e
               for e in check_draft(good.model_copy(update={"test_name": "bug"})))  # fmt: skip
    skip = "import pytest\n\n@pytest.mark.skip\ndef test_bug():\n    pass\n"
    assert any("skip" in e for e in check_draft(good.model_copy(update={"code": skip})))


def test_write_policy_allowed_new_test() -> None:
    p = WritePolicy(allow_source=True, allowed_new_tests=frozenset({"tests/test_repro.py"}))
    p.check("tests/test_repro.py", exists=False)  # the repro file, as new in the patch
    with pytest.raises(PathPolicyError):
        p.check("tests/test_repro.py", exists=True)  # but never editable once it exists
    with pytest.raises(PathPolicyError):
        p.check("tests/test_other.py", exists=False)


def test_validator_treats_repro_as_evidence_not_fix(workspace) -> None:  # type: ignore[no-untyped-def]
    plan = Plan(problem="p", hypothesis_id="H1", files_to_modify=["users_service/api.py"],
                changes=[PlannedChange(file="users_service/api.py", description="d")],
                rollback_strategy="r")  # fmt: skip
    (workspace.root / "tests" / "test_repro.py").write_text("def test_bug():\n    assert False\n")
    policy = WritePolicy(allow_source=True, allowed_new_tests=frozenset({"tests/test_repro.py"}))
    v, dev, _ = validate_patch(workspace, workspace.diff(), plan, policy, max_changed_lines=200,
                               repro_path="tests/test_repro.py")  # fmt: skip
    assert not v.accepted and v.errors == ["no changes were made"]  # repro alone is not a fix
    api = workspace.root / "users_service" / "api.py"
    api.write_text(api.read_text().replace('payload["email"]', 'payload.get("email") or ""'))
    v, dev, _ = validate_patch(workspace, workspace.diff(), plan, policy, max_changed_lines=200,
                               repro_path="tests/test_repro.py")  # fmt: skip
    assert v.accepted, v.errors
    assert dev.files_outside_plan == []


def test_escalation_ladder() -> None:
    assert ESCALATION == {"implement": "plan", "plan": "explore", "explore": "abort"}


def test_levels_l4_l5() -> None:
    base = BaselineResult(image="i", run_id="b", passing=["a"], failing=["t"], collection_errors=[],
                          status=TestRunStatus.FAILED)  # fmt: skip
    target = run(TestRunStatus.PASSED, passed=["a", "t"], kind=TestRunKind.TARGET)
    lint_ok = run(TestRunStatus.PASSED, kind=TestRunKind.LINT)
    kw = {"static_ok": True, "import_check": None, "target": target, "baseline": base,
          "targets": ["t"]}  # fmt: skip
    assert compute_verification(**kw).level == 3
    assert compute_verification(**kw, lint=lint_ok, new_lint=[]).level == 4
    v = compute_verification(**kw, lint=lint_ok, new_lint=["a.py: F401 `os` imported but unused"])
    assert v.level == 3 and "F401" in v.notes[-1]
    infra = run(TestRunStatus.INFRA_ERROR, kind=TestRunKind.LINT)
    assert compute_verification(**kw, lint=infra, new_lint=[]).level == 3
    acc_ok = run(TestRunStatus.PASSED, passed=["x"], kind=TestRunKind.ACCEPTANCE)
    acc_bad = run(TestRunStatus.FAILED, failed=["x"], kind=TestRunKind.ACCEPTANCE)
    assert compute_verification(**kw, lint=lint_ok, new_lint=[], acceptance=acc_ok).level == 5
    assert compute_verification(**kw, lint=lint_ok, new_lint=[], acceptance=acc_bad).level == 4
    # L5 requires L4: acceptance cannot lift a lint failure
    assert compute_verification(**kw, lint=lint_ok, new_lint=["x"], acceptance=acc_ok).level == 3


def test_checkpoint_allow_list_covers_state_types() -> None:
    names = {n for _, n in _state_types()}
    assert {"Budget", "TestRun", "TestRunStatus", "ReproResult", "ApprovalDecision",
            "FailureAnalysis", "Plan", "TerminationReason"} <= names  # fmt: skip
    assert all(m == "swe_agent.schemas.state" for m, _ in _state_types())
