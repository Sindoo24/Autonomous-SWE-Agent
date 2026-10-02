from __future__ import annotations

from swe_agent.repository.pathjail import WritePolicy
from swe_agent.schemas.state import DeviationSeverity, Plan, PlannedChange
from swe_agent.verification.patch_validator import compute_deviation, parse_diff, validate_patch

POLICY = WritePolicy(allow_source=True)


def _plan(files: list[str], n_changes: int = 1) -> Plan:
    return Plan(
        problem="p",
        hypothesis_id="H1",
        files_to_modify=files,
        changes=[PlannedChange(file=files[0], description="d") for _ in range(n_changes)],
        rollback_strategy="git restore",
    )


API = "users_service/api.py"


def _edit(ws, rel: str, old: str, new: str) -> None:  # type: ignore[no-untyped-def]
    p = ws.root / rel
    p.write_text(p.read_text().replace(old, new))


def test_parse_diff_counts() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n b\n"
        "diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n@@ -0,0 +1 @@\n"
        "+x\n"
    )
    s = parse_diff(diff)
    assert s.files == ["x.py", "n.py"] and s.added == 2 and s.removed == 1
    assert ("n.py", "x") in s.added_lines


def test_accepts_minimal_fix(workspace) -> None:  # type: ignore[no-untyped-def]
    _edit(workspace, API, 'payload["email"]', 'payload.get("email") or ""')
    v, dev, stats = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=200
    )
    assert v.accepted, v.errors
    assert dev.severity is DeviationSeverity.NONE
    assert stats.files == [API] and stats.added == 1 and stats.removed == 1


def test_rejects_no_changes(workspace) -> None:  # type: ignore[no-untyped-def]
    v, _, _ = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=200
    )
    assert not v.accepted and "no changes were made" in v.errors


def test_rejects_syntax_error(workspace) -> None:  # type: ignore[no-untyped-def]
    _edit(workspace, API, "def post_users(", "def post_users((")
    v, _, _ = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=200
    )
    assert not v.accepted and any("syntax error" in e for e in v.errors)


def test_rejects_test_tampering_and_protected_files(workspace) -> None:  # type: ignore[no-untyped-def]
    # Simulates a bug in the edit tools letting a test edit through: the validator still blocks.
    _edit(
        workspace,
        "tests/test_api.py",
        "def test_missing_name_is_422():",
        "import pytest\n\n@pytest.mark.skip\ndef test_missing_name_is_422():",
    )
    v, _, _ = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=200
    )
    assert not v.accepted
    assert any("existing test files are read-only" in e for e in v.errors)
    assert any("adds a pytest skip/xfail" in e for e in v.errors)


def test_rejects_oversized_patch(workspace) -> None:  # type: ignore[no-untyped-def]
    p = workspace.root / API
    p.write_text(p.read_text() + "\n".join(f"X{i} = {i}" for i in range(50)) + "\n")
    v, _, _ = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=20
    )
    assert not v.accepted and any("limit 20" in e for e in v.errors)


def test_warns_on_suspicious_code(workspace) -> None:  # type: ignore[no-untyped-def]
    _edit(
        workspace,
        API,
        "store = UserStore()",
        "import subprocess\nstore = UserStore()\nsubprocess.run(['true'])",
    )
    v, _, _ = validate_patch(
        workspace, workspace.diff(), _plan([API]), POLICY, max_changed_lines=200
    )
    assert v.accepted
    assert any("process/eval" in w for w in v.warnings)


def test_deviation_levels() -> None:
    plan = _plan(["a.py"])
    assert compute_deviation(plan, ["a.py"], 5).severity is DeviationSeverity.NONE
    assert compute_deviation(plan, ["a.py", "b.py"], 5).severity is DeviationSeverity.MINOR
    assert compute_deviation(plan, ["a.py", "b.py", "c.py"], 5).severity is DeviationSeverity.MAJOR
    assert compute_deviation(plan, ["a.py"], 40).severity is DeviationSeverity.MINOR
    assert compute_deviation(plan, ["a.py"], 100).severity is DeviationSeverity.MAJOR
    d = compute_deviation(_plan(["a.py", "b.py"]), ["a.py"], 3)
    assert d.severity is DeviationSeverity.MINOR and d.planned_files_untouched == ["b.py"]
