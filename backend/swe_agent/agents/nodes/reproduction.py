"""Recovery: the reproduce node, root-cause analysis, escalation and rollback.

reproduce (plan -> reproduce -> implement)
    Gives the agent fail -> pass evidence for bugs no existing test covers. The model proposes a
    complete test file as structured output (more robust for small models than a tool loop); the
    system writes it, runs it on the UNPATCHED code in the sandbox, and accepts it only if it
    fails for the right reason (an assertion or the buggy exception, not ImportError/NameError/
    collection errors). Up to N attempts with the real pytest evidence as feedback. Failing to
    reproduce is not fatal: the run continues and simply cannot reach VERIFIED.

    Skipped when an existing baseline-failing test already covers the issue (regressions), or
    when a reproduction test already exists (re-plans, re-explores).
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import PurePosixPath
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node, render_evidence
from swe_agent.agents.nodes.testing import select_targets
from swe_agent.agents.state import AgentState
from swe_agent.core.errors import ModelError
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.types import Message
from swe_agent.prompts import templates as T
from swe_agent.prompts.safety import neutralize
from swe_agent.repository.pathjail import is_test_path
from swe_agent.repository.workspace import Workspace
from swe_agent.schemas.state import (
    AgentError,
    ReproDraft,
    ReproResult,
    TestRun,
    TestRunKind,
    TestRunStatus,
)
from swe_agent.verification.patch_validator import TAMPER_PATTERNS

REPRO_BASENAME = "test_swe_agent_repro"
MAX_REPRO_CHARS = 8000
EXAMPLE_TEST_CHARS = 2000
# Failing for these reasons means the test is broken, not that it reproduces the bug.
WRONG_REASON = re.compile(
    r"\b(ImportError|ModuleNotFoundError|NameError|SyntaxError|IndentationError|TabError)\b"
    r"|fixture '[^']+' not found"
)
_IDENT = re.compile(r"^test_[A-Za-z0-9_]*$")


def repro_path_for(ws: Workspace, test_dirs: list[str]) -> str:
    base = "tests" if "tests" in test_dirs else (test_dirs[0] if test_dirs else "")
    for i in range(1, 100):
        name = f"{REPRO_BASENAME}{'' if i == 1 else f'_{i}'}.py"
        rel = f"{base}/{name}" if base else name
        if not ws.jail.resolve(rel).exists():
            return rel
    raise ModelError("could not choose a reproduction test path")


def example_test(ws: Workspace) -> str | None:
    tests = [f for f in ws.ls_files() if f.endswith(".py") and is_test_path(f)
             and PurePosixPath(f).name.startswith("test_")]  # fmt: skip
    for rel in sorted(tests):
        try:
            text = ws.jail.resolve(rel, must_exist=True).read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        if text.strip():
            return f"# {rel}\n{text[:EXAMPLE_TEST_CHARS]}"
    return None


def check_draft(d: ReproDraft) -> list[str]:
    errs: list[str] = []
    if not _IDENT.match(d.test_name):
        errs.append("test_name must be a Python identifier starting with test_")
    if len(d.code) > MAX_REPRO_CHARS:
        errs.append(f"code is too long ({len(d.code)} chars); keep the test small")
    try:
        tree = ast.parse(d.code)
    except SyntaxError as exc:
        return [*errs, f"code has a syntax error at line {exc.lineno}: {exc.msg}"]
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    if d.test_name not in names:
        errs.append(f"code must define a top-level function named {d.test_name!r}")
    for pattern, label in TAMPER_PATTERNS:
        if pattern.search(d.code):
            errs.append(f"code {label}")
    return errs


def judge_repro(run: TestRun, nodeid: str) -> tuple[bool, str]:
    """Does this run on the unpatched code show the test failing for the right reason?"""
    excerpt = next((f"{f.message}\n{f.excerpt[-1200:]}" for f in run.failures
                    if f.nodeid in (nodeid, "(run)") or f.nodeid.endswith(".py")), "")  # fmt: skip
    if run.status in (TestRunStatus.TIMEOUT, TestRunStatus.RESOURCE_LIMIT,
                      TestRunStatus.INFRA_ERROR):  # fmt: skip
        return False, f"the test run did not complete ({run.status.value}). {excerpt}"
    if run.collection_errors or nodeid not in {*run.passed, *run.failed, *run.errors}:
        detail = excerpt or run.output_tail[-1200:]
        return False, ("the test file could not be collected or the test was not found "
                       f"(check imports and the function name). {detail}")  # fmt: skip
    if nodeid in run.passed:
        return False, ("the test PASSES on the current buggy code, so it does not reproduce the "
                       "bug. Assert the correct behaviour described in the issue.")  # fmt: skip
    fail = next((f for f in run.failures if f.nodeid == nodeid), None)
    text = f"{fail.message}\n{fail.excerpt}" if fail else excerpt
    if nodeid in run.errors or WRONG_REASON.search(text):
        return (
            False,
            f"the test fails for the wrong reason (broken test, not the bug): {text[-1200:]}",
        )
    return True, (fail.message if fail else "failed")


@node("reproduce")
async def reproduce(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    sandbox = ctx.sandbox
    if sandbox is None or not ctx.settings.agent.reproduce or state.get("repro") is not None:
        return {}
    ws, _ = ctx.require_workspace()
    baseline = state.get("baseline")
    image = state.get("sandbox_image")
    if baseline is None or not image:
        return {}
    plan = state["plan"]
    existing = select_targets(baseline.failing, plan.tests_to_run, state["issue"])
    if existing:
        ctx.recorder.record("recovery", action="reproduce_skipped", reason="existing failing tests",
                            targets=existing)  # fmt: skip
        return {}

    rel = repro_path_for(ws, state["repo_summary"].test_dirs)
    refs = [e for h in state["hypotheses"].hypotheses for e in h.evidence]
    snippets = render_evidence(ws, refs)
    example = example_test(ws)
    feedback: str | None = None
    runs: list[TestRun] = []
    attempts = ctx.settings.agent.reproduce_attempts
    for attempt in range(1, attempts + 1):
        try:
            draft = await ctx.gateway.structured(
                ModelRole.CODER,
                [
                    Message(role="system", content=T.REPRODUCE_SYSTEM),
                    Message(role="user", content=T.reproduce_task(
                        state["issue"], plan, snippets, example, feedback)),
                ],
                ReproDraft,
                purpose=f"reproduce.attempt{attempt}",
                validate=check_draft,
            )  # fmt: skip
        except ModelError as exc:
            feedback = None
            ctx.recorder.record("recovery", action="reproduce_model_error", attempt=attempt,
                                error=str(exc)[:500])  # fmt: skip
            continue
        path = ws.jail.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft.code, encoding="utf-8")
        run = await asyncio.to_thread(
            sandbox.run_tests, ws, image, TestRunKind.REPRO, selection=[rel]
        )
        runs.append(run)
        nodeid = f"{rel}::{draft.test_name}"
        ok, reason = judge_repro(run, nodeid)
        ctx.recorder.record("recovery", action="reproduce_attempt", attempt=attempt, ok=ok,
                            nodeid=nodeid, test_run=run.id, reason=reason[:500])  # fmt: skip
        if ok:
            repro = ReproResult(
                path=rel,
                test_name=draft.test_name,
                nodeid=nodeid,
                failing_run_id=run.id,
                content=draft.code,
                attempts=attempt,
            )
            # The test verifiably fails on the unpatched code: record it as a baseline failure,
            # so fail -> pass evidence and regression checks treat it like any other test.
            new_baseline = baseline.model_copy(
                update={"failing": sorted({*baseline.failing, nodeid})}
            )
            return {"repro": repro, "baseline": new_baseline, "test_runs": runs}
        feedback = neutralize(reason)
    ws.jail.resolve(rel).unlink(missing_ok=True)
    return {
        "test_runs": runs,
        "errors": [
            AgentError(
                node="reproduce",
                kind="not_reproduced",
                message=f"no valid reproduction test after {attempts} attempts; "
                "continuing without fail->pass evidence",
            )
        ],
    }
