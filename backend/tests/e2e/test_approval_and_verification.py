"""L4 (lint), L5 (acceptance tests), human approval with durable checkpoints.

Approval is exercised end to end: the run pauses, the pause survives the process (resumed from a
separate `swe-agent approve` subprocess), rejection with feedback re-plans, plain rejection stops.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_agent.agents.runner import RunNotFoundError, resume_run, run_status, run_task
from swe_agent.config import SandboxSettings, Settings
from swe_agent.llm.scripted import ScriptedCall, ScriptedProvider, action
from swe_agent.schemas.state import ApprovalDecision
from tests.conftest import git
from tests.e2e.test_graph_execution import (
    BUGGY,
    CALC_HYP,
    CALC_ISSUE,
    OPS_PATH,
    REPRO,
    calc_explore,
    calc_repo,  # noqa: F401 - fixture
    edit,
)
from tests.integration.test_graph_e2e import (
    API,
    HYPOTHESES,
    IMPL,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = pytest.mark.docker

CALC_PLAN_ACC = {
    "problem": "clamp upper branch", "hypothesis_id": "H1", "files_to_modify": [OPS_PATH],
    "changes": [{"file": OPS_PATH, "symbol": "clamp", "description": "return hi when x > hi"}],
    "tests_to_run": ["tests/test_ops.py::test_clamp_high"], "risks": ["none"],
    "rollback_strategy": "git restore calc/ops.py",
}  # fmt: skip
GOOD_FIX = "    if x > hi:\n        return hi\n"


@pytest.fixture
def exec_settings(settings: Settings, sandbox_settings: SandboxSettings) -> Settings:
    settings.sandbox = sandbox_settings.model_copy(update={"enabled": True})
    return settings


def calc_script(fix: str = GOOD_FIX) -> list[Any]:
    return [*calc_explore(), CALC_HYP, CALC_PLAN_ACC, *edit(BUGGY, fix)]


# --------------------------------------------------------------------------- L4 / L5


async def test_new_lint_finding_blocks_l4(calc_repo: Path, exec_settings: Settings) -> None:  # noqa: F811
    script = [
        *calc_explore(), CALC_HYP, CALC_PLAN_ACC,
        action("edit_file", path=OPS_PATH, search="def add(a, b):",
               replace="import os\n\n\ndef add(a, b):"),
        *edit(BUGGY, GOOD_FIX),
    ]  # fmt: skip
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings,
                         provider=ScriptedProvider(script))  # fmt: skip
    ver = out.report["verification"]
    assert out.report["status"] == "VERIFIED" and ver["level"] == 3
    assert any("F401" in n and "L4 not reached" in n for n in ver["notes"])


async def test_preexisting_lint_findings_do_not_block_l4(
    calc_repo: Path,  # noqa: F811
    exec_settings: Settings,
) -> None:
    ops = calc_repo / "calc" / "ops.py"
    ops.write_text("import sys\n\n\n" + ops.read_text())  # unused import already at base
    git(calc_repo, "commit", "-qam", "legacy import")
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings,
                         provider=ScriptedProvider(calc_script()))  # fmt: skip
    assert out.report["verification"]["level"] == 4, out.report["verification"]


async def test_acceptance_tests_give_l5(
    calc_repo: Path,  # noqa: F811
    exec_settings: Settings,
    tmp_path: Path,
) -> None:
    acc = tmp_path / "acceptance"
    acc.mkdir()
    (acc / "test_accept.py").write_text(
        "from calc.ops import clamp\n\n\ndef test_bounds():\n"
        "    assert clamp(50, 0, 10) == 10\n    assert clamp(5, 0, 10) == 5\n"
    )
    exec_settings.agent.acceptance_dir = acc
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings,
                         provider=ScriptedProvider(calc_script()))  # fmt: skip
    ver = out.report["verification"]
    assert ver["level"] == 5, ver
    assert any("acceptance tests pass" in e for e in ver["evidence"])
    ws = Path(out.report["repository"]["workspace_path"])
    assert not (ws / "tests_acceptance").exists()  # never entered the agent's workspace


async def test_failing_acceptance_tests_cap_at_l4(
    calc_repo: Path,  # noqa: F811
    exec_settings: Settings,
    tmp_path: Path,
) -> None:
    acc = tmp_path / "acceptance"
    acc.mkdir()
    (acc / "test_accept.py").write_text(
        "from calc.ops import clamp\n\n\ndef test_strict():\n    assert clamp(-1, 0, 10) == -1\n"
    )
    exec_settings.agent.acceptance_dir = acc
    out = await run_task(str(calc_repo), CALC_ISSUE, exec_settings,
                         provider=ScriptedProvider(calc_script()))  # fmt: skip
    ver = out.report["verification"]
    assert ver["level"] == 4 and any("L5 not reached" in n for n in ver["notes"])


# --------------------------------------------------------------------------- approval


def users_script() -> list[Any]:
    return [*explore_script(), HYPOTHESES, PLAN, REPRO, *implement_script()]


async def _paused(users_repo: Path, settings: Settings) -> Any:
    settings.agent.approval = "manual"
    out = await run_task(
        str(users_repo), ISSUE, settings, provider=ScriptedProvider(users_script())
    )
    assert out.pending_approval is not None and out.report == {}
    return out


async def test_run_pauses_for_approval_and_resumes(
    users_repo: Path, exec_settings: Settings
) -> None:
    out = await _paused(users_repo, exec_settings)
    req = out.pending_approval
    assert req["verification"]["status"] == "VERIFIED" and req["files_changed"]
    assert (out.artifacts_dir / "approval_request.json").exists()
    pending = (out.artifacts_dir / "pending.patch").read_text()
    assert "raise ValidationError" in pending
    assert not (out.artifacts_dir / "report.json").exists()  # nothing finalized yet

    status = await run_status(out.run_id, exec_settings)
    assert status["state"] == "awaiting_approval" and status["next"] == ["human_approval"]

    # Approve with a provider that must not be called: approval only finalizes.
    done = await resume_run(out.run_id, ApprovalDecision(approved=True, decided_by="sindoori"),
                            exec_settings, provider=ScriptedProvider([]))  # fmt: skip
    r = done.report
    assert r["termination_reason"] == "patch_proposed" and r["status"] == "VERIFIED"
    assert r["approval"] == {"approved": True, "feedback": None, "retry": False,
                             "decided_by": "sindoori"}  # fmt: skip
    assert r["patch_applies_cleanly"] is True
    # the exported patch applies to the user's original repository
    patch = (done.artifacts_dir / "final.patch").read_text()
    await asyncio.to_thread(subprocess.run, ["git", "apply", "--check", "-"], input=patch,
                            text=True, cwd=users_repo, check=True)  # fmt: skip
    # the trajectory continues in the same file, with monotonic sequence numbers
    seqs = [json.loads(x)["seq"] for x in
            (done.artifacts_dir / "trajectory.jsonl").read_text().splitlines()]  # fmt: skip
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert (await run_status(out.run_id, exec_settings))["state"] == "finished"
    with pytest.raises(RunNotFoundError):  # cannot approve twice
        await resume_run(out.run_id, ApprovalDecision(approved=True), exec_settings,
                         provider=ScriptedProvider([]))  # fmt: skip


async def test_approval_survives_process_restart(
    users_repo: Path, exec_settings: Settings, tmp_path: Path
) -> None:
    """Pause in this process; approve from a brand-new `swe-agent` process."""
    out = await _paused(users_repo, exec_settings)
    env_file = tmp_path / "resume.env"
    s = exec_settings
    env_file.write_text(
        f"SWE_WORKSPACE_ROOT={s.workspace_root}\nSWE_ARTIFACTS_ROOT={s.artifacts_root}\n"
        f"SWE_CHECKPOINT_DB={s.checkpoint_db}\nAGENT_APPROVAL=manual\n"
        f"SANDBOX_BASE_IMAGE={s.sandbox.base_image}\nSANDBOX_MANYLINUX_MAX={s.sandbox.manylinux_max}\n"
        "MODEL_BASE_URL=http://127.0.0.1:9\n"  # unreachable: approving must not call the model
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SANDBOX_", "SWE_", "AGENT_"))}
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "swe_agent", "--env-file", str(env_file), "approve", out.run_id,
         "--by", "reviewer"],
        capture_output=True, text=True, env=env, timeout=300,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (
        "VERIFIED (level 4)" in proc.stdout and "patch applies cleanly to base: True" in proc.stdout
    )
    report = json.loads((out.artifacts_dir / "report.json").read_text())
    assert report["approval"]["decided_by"] == "reviewer"


async def test_reject_with_feedback_replans(users_repo: Path, exec_settings: Settings) -> None:
    out = await _paused(users_repo, exec_settings)

    def replan(call: ScriptedCall) -> dict[str, Any]:
        assert "human reviewer REJECTED" in call.messages[-1].content
        assert "say which field" in call.messages[-1].content
        return PLAN

    better = '        raise ValidationError("email", "email is required (field: email)")\n'
    resumed = await resume_run(
        out.run_id,
        ApprovalDecision(approved=False, feedback="say which field is missing", retry=True),
        exec_settings,
        provider=ScriptedProvider([
            replan,  # reproduce is skipped: the reproduction test already exists
            action("edit_file", path=API,
                   search='        raise ValidationError("email", "email is required")\n',
                   replace=better),
            action("finish", **IMPL),
        ]),
    )  # fmt: skip
    assert resumed.pending_approval is not None  # paused again for the revised patch
    done = await resume_run(resumed.run_id, ApprovalDecision(approved=True), exec_settings,
                            provider=ScriptedProvider([]))  # fmt: skip
    assert done.report["termination_reason"] == "patch_proposed"
    assert done.report["iterations"] == 2
    assert "(field: email)" in (done.artifacts_dir / "final.patch").read_text()


async def test_plain_rejection_stops(users_repo: Path, exec_settings: Settings) -> None:
    out = await _paused(users_repo, exec_settings)
    done = await resume_run(out.run_id, ApprovalDecision(approved=False, feedback="not needed"),
                            exec_settings, provider=ScriptedProvider([]))  # fmt: skip
    r = done.report
    assert r["termination_reason"] == "rejected_by_human"
    assert r["verification_before_termination"]["status"] == "VERIFIED"
    assert r["approval"]["approved"] is False
