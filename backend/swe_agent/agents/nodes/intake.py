"""Entry nodes: `intake` validates the issue text; `prepare_repo` creates the isolated
workspace, the symbol index and the repository summary."""

from __future__ import annotations

import re
from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node
from swe_agent.agents.state import AgentState
from swe_agent.repository.summary import build_summary
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import prepare_workspace
from swe_agent.schemas.state import (
    AgentError,
    RepoRef,
    TerminationReason,
)

MAX_ISSUE_CHARS = 20_000


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@node("intake")
async def intake(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    issue = _CONTROL.sub("", state.get("issue", "")).strip()
    if not issue:
        return {
            "termination_reason": TerminationReason.INVALID_INPUT,
            "errors": [AgentError(node="intake", kind="invalid_input", message="empty issue")],
        }
    if len(issue) > MAX_ISSUE_CHARS:
        return {
            "termination_reason": TerminationReason.INVALID_INPUT,
            "errors": [
                AgentError(
                    node="intake",
                    kind="invalid_input",
                    message=f"issue longer than {MAX_ISSUE_CHARS} chars",
                )
            ],
        }
    return {
        "issue": issue,
        "validation_attempts": 0,
        "validation_feedback": None,
        "failure_feedback": None,
        "failure_analysis": None,
    }


@node("prepare_repo")
async def prepare_repo(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    repo = state["repo"]
    # per run: the same task can be run many times (retries, baselines, benchmark repeats)
    dest = ctx.settings.workspace_root.resolve() / ctx.task_id / ctx.run_id / "repo"
    ws = prepare_workspace(repo.source, dest, task_id=ctx.task_id)
    files = ws.ls_files()
    index = SymbolIndex.build(ws.root, files)
    ctx.workspace, ctx.index = ws, index
    summary = build_summary(ws.root, files, index, state["issue"])
    if not ctx.settings.agent.candidate_seeding:  # ablation: explore without the pre-ranking
        summary = summary.model_copy(update={"candidates": []})
    return {
        "repo": RepoRef(
            source=repo.source,
            workspace_path=str(ws.root),
            branch=ws.branch,
            base_commit=ws.base_commit,
        ),
        "repo_summary": summary,
    }
