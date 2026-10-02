"""Understanding nodes: `explore` (read-only tool loop that finds the responsible code) and
`hypothesize` (root-cause hypotheses backed by checked evidence)."""

from __future__ import annotations

from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.loop import run_tool_loop
from swe_agent.agents.nodes.common import check_evidence, node, render_evidence
from swe_agent.agents.state import AgentState
from swe_agent.core.errors import PathPolicyError
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.types import Message
from swe_agent.prompts import templates as T
from swe_agent.schemas.state import (
    Findings,
    HypothesisSet,
)
from swe_agent.tools.base import ToolContext


@node("explore")
async def explore(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, index = ctx.require_workspace()
    tool_ctx = ToolContext(workspace=ws, index=index, node="explore")

    def validate(f: Findings) -> list[str]:
        errs = check_evidence(ws, f.evidence)
        if not f.evidence:
            errs.append("provide at least one evidence item (file + line range)")
        for rel in f.relevant_files:
            try:
                ws.jail.resolve(rel, must_exist=True)
            except PathPolicyError as exc:
                errs.append(f"relevant_files: {exc}")
        return errs

    findings = await run_tool_loop(
        ctx,
        node="explore",
        role=ModelRole.REASONING,
        system=T.EXPLORE_SYSTEM,
        task=T.explore_task(
            state["issue"],
            state["repo_summary"],
            state.get("failure_feedback") if state.get("rollbacks") else None,
        ),
        finish_model=Findings,
        finish_description="Report exploration findings: summary, relevant files and symbols, "
        "and evidence (file + line range you actually inspected).",
        max_steps=ctx.settings.budget.explore_max_steps,
        tool_ctx=tool_ctx,
        finish_validate=validate,
    )
    return {"findings": findings}


@node("hypothesize")
async def hypothesize(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    findings = state["findings"]
    snippets = render_evidence(ws, findings.evidence)

    def validate(hs: HypothesisSet) -> list[str]:
        errs: list[str] = []
        for h in hs.hypotheses:
            if not h.evidence:
                errs.append(f"hypothesis {h.id}: cite at least one evidence item")
            errs += [f"hypothesis {h.id}: {e}" for e in check_evidence(ws, h.evidence)]
        return errs

    hs = await ctx.gateway.structured(
        ModelRole.REASONING,
        [
            Message(role="system", content=T.HYPOTHESIZE_SYSTEM),
            Message(role="user", content=T.hypothesize_task(state["issue"], findings, snippets)),
        ],
        HypothesisSet,
        purpose="hypothesize",
        validate=validate,
    )
    # Stable ids regardless of what the model chose.
    for i, h in enumerate(hs.hypotheses, start=1):
        h.id = f"H{i}"
    return {"hypotheses": hs}
