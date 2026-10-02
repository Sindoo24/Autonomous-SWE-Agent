"""Full graph with real Docker execution and a scripted model.

Proves the execution loop: implement -> sandbox -> pytest -> parse -> verify | analyze -> replan.
Model responses are scripted; this measures orchestration, not model quality.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_agent.agents.runner import run_task
from swe_agent.config import SandboxSettings, Settings
from swe_agent.llm.scripted import ScriptedCall, ScriptedProvider, action
from tests.conftest import git
from tests.integration.test_graph_e2e import (
    HYPOTHESES,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = pytest.mark.docker

OPS = """\
def add(a, b):
    return a + b


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return lo
    return x


def mean(xs):
    return sum(xs) / len(xs)
"""
TESTS = """\
from calc.ops import add, clamp, mean


def test_add():
    assert add(2, 3) == 5


def test_clamp_low():
    assert clamp(-5, 0, 10) == 0


def test_clamp_high():
    assert clamp(50, 0, 10) == 10


def test_mean():
    assert mean([1, 2, 3]) == 2
"""
CALC_ISSUE = "clamp() returns the lower bound when x is above the upper bound (test_clamp_high)."
OPS_PATH = "calc/ops.py"
BUGGY = "    if x > hi:\n        return lo\n"
CALC_FINDINGS = {
    "summary": "clamp returns lo in the x > hi branch",
    "relevant_files": [OPS_PATH],
    "relevant_symbols": ["clamp"],
    "evidence": [{"path": OPS_PATH, "start_line": 5, "end_line": 10, "why": "wrong bound"}],
}
CALC_HYP = {
    "hypotheses": [
        {
            "id": "h",
            "statement": "x > hi branch returns lo instead of hi",
            "evidence": CALC_FINDINGS["evidence"],
            "confidence": "high",
        }
    ]
}
CALC_PLAN = {
    "problem": "clamp upper branch",
    "hypothesis_id": "H1",
    "files_to_modify": [OPS_PATH],
    "changes": [{"file": OPS_PATH, "symbol": "clamp", "description": "return hi when x > hi"}],
    "tests_to_run": ["tests/test_ops.py::test_clamp_high"],
    "risks": [],
    "rollback_strategy": "git restore calc/ops.py",
}
IMPL = {"summary": "fix upper branch", "root_cause": "x > hi branch returned lo"}


def analysis(right_place: bool = True) -> Any:
    """Scripted root-cause analysis; asserts it was given the real pytest evidence."""

    def respond(call: ScriptedCall) -> dict[str, Any]:
        text = call.messages[-1].content
        assert "Test evidence" in text and "test_clamp" in text
        return {
            "root_cause": "the upper branch still does not return hi",
            "fix_in_right_place": right_place,
            "revised_hypothesis": "return hi when x > hi",
        }

    return respond


@pytest.fixture
def calc_repo(tmp_path: Path) -> Path:
    root = tmp_path / "origin" / "calc"
    (root / "calc").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "calc" / "__init__.py").write_text("")
    (root / "calc" / "ops.py").write_text(OPS)
    (root / "tests" / "test_ops.py").write_text(TESTS)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "calc")
    return root


@pytest.fixture
def exec_settings(settings: Settings, sandbox_settings: SandboxSettings) -> Settings:
    settings.sandbox = sandbox_settings.model_copy(update={"enabled": True})
    return settings


def calc_explore() -> list[Any]:
    return [action("read_file", path=OPS_PATH), action("finish", **CALC_FINDINGS)]


def edit(search: str, replace: str) -> list[Any]:
    return [
        action("edit_file", path=OPS_PATH, search=search, replace=replace),
        action("finish", **IMPL),
    ]


def events(art_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (art_dir / "trajectory.jsonl").read_text().splitlines()]


async def test_verified_fix_after_replanning(calc_repo: Path, exec_settings: Settings) -> None:
    def replan(call: ScriptedCall) -> dict[str, Any]:
        text = call.messages[-1].content
        assert "executed in the sandbox and FAILED" in text
        assert "test_clamp_high" in text and "assert" in text  # real pytest evidence
        return CALC_PLAN

    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, "    if x > hi:\n        return x\n"),  # wrong fix: target still fails
            analysis(),
            replan,
            *edit("    if x > hi:\n        return x\n", "    if x > hi:\n        return hi\n"),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed", r["errors"]
    assert r["status"] == "VERIFIED" and r["verification"]["level"] == 4
    assert any("fail -> pass" in e for e in r["verification"]["evidence"])
    assert any("no new lint findings" in e for e in r["verification"]["evidence"])
    assert r["reproduction"] is None  # an existing failing test covers the issue
    assert r["root_cause_analyses"][0]["fix_in_right_place"] is True
    assert r["patch_applies_cleanly"] is True
    assert r["baseline"]["failing"] == ["tests/test_ops.py::test_clamp_high"]
    assert r["targets"] == ["tests/test_ops.py::test_clamp_high"]
    assert r["iterations"] == 2
    assert [f["category"] for f in r["failure_history"]] == ["test_failure"]
    kinds = [t["kind"] for t in r["tests_executed"]]
    assert kinds == ["baseline", "import_check", "target", "import_check", "target", "lint"]
    assert r["test_results"]["failed"] == [] and len(r["test_results"]["passed"]) == 4
    assert "+        return hi" in (out.artifacts_dir / "final.patch").read_text()
    nodes = [e["node"] for e in events(out.artifacts_dir) if e["event"] == "node_started"]
    assert nodes == [
        "intake",
        "prepare_repo",
        "baseline_tests",
        "explore",
        "hypothesize",
        "plan",
        "reproduce",
        "implement",
        "validate_patch",
        "run_tests",
        "analyze_failure",
        "plan",
        "reproduce",
        "implement",
        "validate_patch",
        "run_tests",
        "verify",
        "human_approval",
        "finalize",
    ]
    assert r["trace"]["test_runs"] == 6


async def test_regression_is_detected_and_repaired(
    calc_repo: Path, exec_settings: Settings
) -> None:
    breaking = "def clamp(x, lo, hi):\n    return min(x, hi)\n"  # fixes high, breaks low
    original = (
        "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n" + BUGGY + "    return x\n"
    )
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(original, breaking),
            analysis(),
            lambda call: (
                (
                    "regressions" in call.messages[-1].content
                    and "test_clamp_low" in call.messages[-1].content
                )
                and CALC_PLAN
            ),
            *edit(breaking, "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["status"] == "VERIFIED", (r["errors"], r["failure_history"])
    assert r["failure_history"][0]["category"] == "regression"
    assert r["failure_history"][0]["regressions"] == ["tests/test_ops.py::test_clamp_low"]


async def test_stagnation_escalates_to_explore_then_stops(
    calc_repo: Path, exec_settings: Settings
) -> None:
    """Same failure twice -> escalate plan -> explore with a rollback to the base commit;
    a third time -> stop with repeated_failure."""
    third = "    if x > hi:\n        return x  # third\n"
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, "    if x > hi:\n        return x\n"),  # failure A (1st)
            analysis(),
            CALC_PLAN,
            *edit(
                "    if x > hi:\n        return x\n", "    if x > hi:\n        return x  # retry\n"
            ),  # failure A (2nd) -> escalate to explore, workspace rolled back
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, third),  # only matches because the rollback restored the buggy file
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "repeated_failure", r["errors"]
    assert r["status"] == "NOT VERIFIED" and r["rollbacks"] == 1
    assert [f["route"] for f in r["failure_history"]] == ["plan", "explore", "plan"]
    rec = [e["data"]["action"] for e in events(out.artifacts_dir) if e["event"] == "recovery"]
    assert "escalate" in rec and "rollback" in rec
    assert (out.artifacts_dir / "final.patch").exists()  # best partial patch still exported
    assert "# third" in (out.artifacts_dir / "final.patch").read_text()
    assert "# retry" not in (out.artifacts_dir / "final.patch").read_text()


async def test_oscillation_stops(calc_repo: Path, exec_settings: Settings) -> None:
    """A re-plan that leaves the failing patch exactly as it was is not progress."""
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, "    if x > hi:\n        return x\n"),
            analysis(),
            CALC_PLAN,
            action("finish", **IMPL),  # changes nothing: same diff as iteration 1
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    assert out.report["termination_reason"] == "oscillation"


async def test_analysis_can_send_run_back_to_explore(
    calc_repo: Path, exec_settings: Settings
) -> None:
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit("def add(a, b):\n    return a + b", "def add(a, b):\n    return b + a"),
            analysis(right_place=False),  # the patch touched code off the failing path
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, "    if x > hi:\n        return hi\n"),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["status"] == "VERIFIED", (r["errors"], r["failure_history"])
    assert r["rollbacks"] == 1 and r["failure_history"][0]["route"] == "explore"
    patch = (out.artifacts_dir / "final.patch").read_text()
    assert "return b + a" not in patch  # the off-path edit was rolled back


async def test_iteration_budget(calc_repo: Path, exec_settings: Settings) -> None:
    exec_settings.budget.max_iterations = 1
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            *edit(BUGGY, "    if x > hi:\n        return x\n"),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    assert out.report["termination_reason"] == "iterations_exhausted"
    assert out.report["test_results"]["failed"] == ["tests/test_ops.py::test_clamp_high"]


async def test_syntax_error_routes_to_implement(calc_repo: Path, exec_settings: Settings) -> None:
    # valid Python for ast, but fails at import time -> classified as the patch's import error
    # -> routed straight back to implement (not to re-planning)
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            action(
                "edit_file",
                path=OPS_PATH,
                search="def add(a, b):",
                replace="import calc_missing_helper\n\n\ndef add(a, b):",
            ),
            *edit(BUGGY, "    if x > hi:\n        return hi\n"),
            lambda call: (
                "FAILED" in call.messages[1].content
                and action(
                    "edit_file",
                    path=OPS_PATH,
                    search="import calc_missing_helper\n\n\n",
                    replace="",
                )
            ),
            action("finish", **IMPL),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["status"] == "VERIFIED", (r["errors"], r["failure_history"])
    cat = r["failure_history"][0]["category"]
    assert cat in {"import_error", "missing_dependency"} and cat == "import_error"


REPRO = {
    "test_name": "test_missing_email_returns_422",
    "code": (
        "from users_service.api import post_users\n\n\n"
        "def test_missing_email_returns_422():\n"
        '    status, body = post_users({"name": "Ada"})\n'
        "    assert status == 422\n"
        '    assert body["field"] == "email"\n'
    ),
    "expected_failure": "returns 500 instead of 422",
}
REPRO_NODE = "tests/test_swe_agent_repro.py::test_missing_email_returns_422"


async def test_reproduce_gives_fail_to_pass_evidence(
    users_repo: Path, exec_settings: Settings
) -> None:
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, REPRO, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed", r["errors"]
    assert r["status"] == "VERIFIED" and r["verification"]["level"] == 4
    assert r["reproduction"]["test"] == REPRO_NODE and r["reproduction"]["attempts"] == 1
    assert REPRO_NODE in r["baseline"]["failing"] and REPRO_NODE in r["targets"]
    assert any(REPRO_NODE in e for e in r["verification"]["evidence"])
    kinds = [t["kind"] for t in r["tests_executed"]]
    assert kinds == ["baseline", "repro", "import_check", "target", "lint"]
    patch = (out.artifacts_dir / "final.patch").read_text()
    assert "b/tests/test_swe_agent_repro.py" in patch  # the regression test ships with the fix
    assert r["patch_applies_cleanly"] is True


async def test_reproduce_retries_with_evidence(users_repo: Path, exec_settings: Settings) -> None:
    broken = {**REPRO, "code": "from users_service.api import no_such_name\n\n\n"
              "def test_missing_email_returns_422():\n    assert no_such_name\n"}  # fmt: skip
    passes = {
        **REPRO,
        "code": REPRO["code"]
        .replace("== 422", "== 500")
        .replace('    assert body["field"] == "email"\n', ""),
    }  # asserts the buggy behaviour

    def second(call: ScriptedCall) -> dict[str, Any]:
        last = call.messages[-1].content
        assert "REJECTED" in last and "could not be collected" in last
        return passes

    def third(call: ScriptedCall) -> dict[str, Any]:
        assert "PASSES on the current buggy code" in call.messages[-1].content
        return REPRO

    provider = ScriptedProvider(
        [*explore_script(), HYPOTHESES, PLAN, broken, second, third, *implement_script()]
    )
    out = await run_task(str(users_repo), ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["status"] == "VERIFIED" and r["reproduction"]["attempts"] == 3


async def test_unreproduced_bug_continues_but_is_not_verified(
    users_repo: Path, exec_settings: Settings
) -> None:
    exec_settings.agent.reproduce_attempts = 1
    passes = {**REPRO, "code": "def test_missing_email_returns_422():\n    assert True\n"}
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, passes, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed"
    assert r["status"] == "NOT VERIFIED" and r["verification"]["level"] == 1
    assert r["reproduction"] is None
    assert any(e["kind"] == "not_reproduced" for e in r["errors"])
    assert "test_swe_agent_repro" not in (out.artifacts_dir / "final.patch").read_text()


async def test_no_fail_to_pass_evidence_is_not_verified(
    users_repo: Path, exec_settings: Settings
) -> None:
    exec_settings.agent.reproduce = False
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed"
    assert r["status"] == "NOT VERIFIED" and r["verification"]["level"] == 1
    assert any("no test demonstrates the fix" in n for n in r["verification"]["notes"])
    assert r["test_results"]["failed"] == [] and len(r["test_results"]["passed"]) == 3


async def test_env_setup_failure_stops_before_model_calls(
    calc_repo: Path, exec_settings: Settings
) -> None:
    (calc_repo / "requirements.txt").write_text("no-such-package-swe-agent-xyz==9.9.9\n")
    git(calc_repo, "add", "-A")
    git(calc_repo, "commit", "-qm", "deps")
    provider = ScriptedProvider([])
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    assert out.report["termination_reason"] == "env_setup"
    assert provider.calls == []


async def test_existing_tests_stay_protected_under_execution(
    calc_repo: Path, exec_settings: Settings
) -> None:
    provider = ScriptedProvider(
        [
            *calc_explore(),
            CALC_HYP,
            CALC_PLAN,
            action("edit_file", path="tests/test_ops.py", search="== 10", replace="== 0"),
            *edit(BUGGY, "    if x > hi:\n        return hi\n"),
        ]
    )
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings, provider=provider)
    r = out.report
    assert r["status"] == "VERIFIED"
    ws = Path(r["repository"]["workspace_path"])
    assert "== 10" in (ws / "tests" / "test_ops.py").read_text()
    errs = [
        e["data"]
        for e in events(out.artifacts_dir)
        if e["event"] == "tool_call" and e["data"].get("status") == "error"
    ]
    assert errs[0]["error_type"] == "policy_violation"
