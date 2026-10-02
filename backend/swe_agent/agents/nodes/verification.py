"""`verify`: compute the verification level (L1-L5) from stored test runs, lint findings and
optional acceptance tests. Deterministic: the model's own claims are never consulted."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node
from swe_agent.agents.state import AgentState
from swe_agent.sandbox.docker import SandboxError
from swe_agent.schemas.state import (
    TestRun,
    TestRunKind,
)
from swe_agent.verification.levels import compute_verification


@node("verify")
async def verify(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    runs = state.get("test_runs", [])
    target = next((r for r in reversed(runs) if r.kind is TestRunKind.TARGET), None)
    imp = next((r for r in reversed(runs) if r.kind is TestRunKind.IMPORT_CHECK), None)
    # An import check older than the latest target run belongs to an earlier iteration.
    if imp is not None and target is not None and runs.index(imp) < runs.index(target) - 1:
        imp = None
    history = state.get("patch_history", [])
    static_ok = bool(history) and history[-1].validation.accepted
    kwargs: dict[str, Any] = {
        "static_ok": static_ok,
        "import_check": imp,
        "target": target,
        "baseline": state.get("baseline"),
        "targets": state.get("targets", []),
    }
    base = compute_verification(**kwargs)
    new_runs: list[TestRun] = []
    sandbox, image = ctx.sandbox, state.get("sandbox_image")
    if base.level >= 3 and sandbox is not None and image:
        agent = ctx.settings.agent
        try:
            if agent.lint:
                files = [f for _, _, f in ws.numstat() if f.endswith(".py")]
                lint_run, new = await asyncio.to_thread(sandbox.lint, ws, image, files)
                new_runs.append(lint_run)
                kwargs |= {"lint": lint_run, "new_lint": new}
            if agent.acceptance_dir is not None:
                extra = acceptance_files(agent.acceptance_dir)
                if extra:
                    acc = await asyncio.to_thread(
                        sandbox.run_tests, ws, image, TestRunKind.ACCEPTANCE, extra_files=extra
                    )
                    new_runs.append(acc)
                    kwargs["acceptance"] = acc
        except SandboxError as exc:
            ctx.recorder.record("recovery", action="verify_extra_failed", error=str(exc)[:300])
        base = compute_verification(**kwargs)
    return {"verification": base, "test_runs": new_runs}


def acceptance_files(directory: Path) -> dict[str, Path]:
    """Acceptance tests live outside the workspace; they are copied into the sandbox snapshot
    only (under tests_acceptance/), so the agent never sees them."""
    root = Path(directory).expanduser().resolve()
    return {
        f"tests_acceptance/{p.name}": p
        for p in sorted(root.glob("test_*.py"))
        if p.is_file() and not p.is_symlink()
    }
