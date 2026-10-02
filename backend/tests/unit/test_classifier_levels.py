from __future__ import annotations

from swe_agent.agents.nodes.testing import changed_modules, project_packages, select_targets
from swe_agent.schemas.state import (
    BaselineResult,
    FailureCategory,
    TestCaseFailure,
    TestRun,
    TestRunKind,
    TestRunStatus,
    VerificationStatus,
)
from swe_agent.verification.classifier import classify, repeated
from swe_agent.verification.levels import compute_verification

A, B, C = "tests/t.py::test_a", "tests/t.py::test_b", "tests/t.py::test_c"
PK = {"pkg", "tests"}


def base(passing: list[str], failing: list[str], coll: list[str] | None = None) -> BaselineResult:
    return BaselineResult(
        image="i",
        run_id="b",
        passing=passing,
        failing=failing,
        collection_errors=coll or [],
        status=TestRunStatus.FAILED,
    )


def run(
    status: TestRunStatus,
    passed: list[str] = (),
    failed: list[str] = (),  # type: ignore[assignment]
    coll: list[str] = (),
    text: str = "",
    kind: TestRunKind = TestRunKind.TARGET,
) -> TestRun:  # type: ignore[assignment]
    return TestRun(
        id="r",
        kind=kind,
        status=status,
        command=[],
        exit_code=1,
        passed=list(passed),
        failed=list(failed),
        collection_errors=list(coll),
        duration_ms=1,
        output_tail=text,
        failures=[
            TestCaseFailure(nodeid=f, kind="failure", message="m", excerpt=text) for f in failed
        ],
    )


def test_pass_when_targets_fixed_and_no_regressions() -> None:
    assert classify(run(TestRunStatus.PASSED, [A, B, C]), base([A, B], [C]), [C], PK) is None


def test_unrelated_preexisting_failure_does_not_block() -> None:
    assert classify(run(TestRunStatus.FAILED, [A, B], [C]), base([A, B], [C]), [], PK) is None


def test_regression_beats_target_failure() -> None:
    a = classify(run(TestRunStatus.FAILED, [C], [A, B]), base([A, B], [C]), [C], PK)
    assert a and a.category is FailureCategory.REGRESSION and a.regressions == [A, B]
    assert a.route == "plan"


def test_target_still_failing() -> None:
    a = classify(run(TestRunStatus.FAILED, [A, B], [C]), base([A, B], [C]), [C], PK)
    assert a and a.category is FailureCategory.TEST_FAILURE and a.still_failing_targets == [C]


def test_collection_categories() -> None:
    b = base([A], [])
    syn = run(TestRunStatus.FAILED, coll=["tests/t.py"], text="E   SyntaxError: invalid syntax")
    assert classify(syn, b, [], PK).category is FailureCategory.SYNTAX_ERROR  # type: ignore[union-attr]
    dep = run(
        TestRunStatus.FAILED,
        coll=["tests/t.py"],
        text="ModuleNotFoundError: No module named 'requests'",
    )
    assert classify(dep, b, [], PK).category is FailureCategory.MISSING_DEPENDENCY  # type: ignore[union-attr]
    own = run(
        TestRunStatus.FAILED,
        coll=["tests/t.py"],
        text="ModuleNotFoundError: No module named 'pkg.missing'",
    )
    assert classify(own, b, [], PK).category is FailureCategory.IMPORT_ERROR  # type: ignore[union-attr]
    added = classify(dep, b, [], PK, patch_added_text="import requests")
    assert added and added.category is FailureCategory.IMPORT_ERROR and added.route == "implement"
    # a module that already failed to collect at baseline is not a new failure
    assert (
        classify(
            run(TestRunStatus.FAILED, [A], coll=["tests/x.py"]),
            base([A], [], ["tests/x.py"]),
            [],
            PK,
        )
        is None
    )


def test_timeout_resource_infra_usage() -> None:
    b = base([A], [])
    cases = {
        TestRunStatus.TIMEOUT: FailureCategory.TIMEOUT,
        TestRunStatus.RESOURCE_LIMIT: FailureCategory.RESOURCE_LIMIT,
        TestRunStatus.INFRA_ERROR: FailureCategory.SANDBOX_ERROR,
        TestRunStatus.USAGE_ERROR: FailureCategory.INVALID_SELECTION,
    }
    for status, cat in cases.items():
        a = classify(run(status), b, [], PK)
        assert a and a.category is cat


def test_signature_stable_and_repeated() -> None:
    b = base([A, B], [C])
    a1 = classify(run(TestRunStatus.FAILED, [A, B], [C], text='File "x.py", line 3'), b, [C], PK)
    a2 = classify(run(TestRunStatus.FAILED, [A, B], [C], text='File "x.py", line 9'), b, [C], PK)
    assert a1 and a2 and a1.signature == a2.signature  # line numbers do not matter
    assert repeated(a2, [a1.signature]) and not repeated(a2, [])


def test_levels() -> None:
    b = base([A, B], [C])
    imp_ok = run(TestRunStatus.PASSED, ["pkg.m"], kind=TestRunKind.IMPORT_CHECK)
    v3 = compute_verification(
        static_ok=True,
        import_check=imp_ok,
        target=run(TestRunStatus.PASSED, [A, B, C]),
        baseline=b,
        targets=[C],
    )
    assert v3.level == 3 and v3.status is VerificationStatus.VERIFIED
    v2 = compute_verification(
        static_ok=True,
        import_check=imp_ok,
        target=run(TestRunStatus.FAILED, [C], [A]),
        baseline=b,
        targets=[C],
    )
    assert v2.level == 2 and v2.status is VerificationStatus.NOT_VERIFIED
    v1 = compute_verification(
        static_ok=True,
        import_check=imp_ok,
        target=run(TestRunStatus.PASSED, [A, B]),
        baseline=base([A, B], []),
        targets=[],
    )
    assert v1.level == 1 and "no test demonstrates the fix" in v1.notes[0]
    bad_imp = run(TestRunStatus.FAILED, kind=TestRunKind.IMPORT_CHECK)
    v0 = compute_verification(
        static_ok=True,
        import_check=bad_imp,
        target=run(TestRunStatus.PASSED, [A, B, C]),
        baseline=b,
        targets=[C],
    )
    assert v0.level == 0


def test_select_targets() -> None:
    failing = [
        "tests/test_ops.py::test_clamp_high",
        "tests/test_io.py::test_read",
        "tests/other/test_x.py::TestK::test_y[1]",
    ]
    assert select_targets(failing, ["tests/test_ops.py::test_clamp_high"], "") == [failing[0]]
    assert select_targets(failing, ["tests/test_io.py"], "") == [failing[1]]
    assert select_targets(failing, ["tests/other"], "") == [failing[2]]
    assert select_targets(failing, [], "test_read breaks on empty files") == [failing[1]]
    assert select_targets(failing, [], "reading is broken") == []  # 'read' != 'test_read'
    assert select_targets(failing, ["tests/test_o"], "") == []  # no partial-name prefix match


def test_changed_modules_and_packages() -> None:
    files = [
        "pkg/a.py",
        "pkg/__init__.py",
        "src/lib/core.py",
        "tests/test_a.py",
        "README.md",
        "bad-name/x.py",
        "setup.py",
    ]
    assert changed_modules(files) == ["lib.core", "pkg", "pkg.a"]
    assert project_packages(files) >= {"pkg", "lib", "tests", "setup"}
