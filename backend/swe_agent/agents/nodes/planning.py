"""`plan`: a typed implementation plan (files, changes, tests, risks, rollback). When re-planning
it receives the real pytest evidence, the root-cause analysis and any reviewer feedback."""

from __future__ import annotations

from typing import Any

from swe_agent.agents.context import RunContext
from swe_agent.agents.nodes.common import node, render_evidence
from swe_agent.agents.nodes.patching import IMPLEMENT_POLICY
from swe_agent.agents.state import AgentState
from swe_agent.core.errors import PathPolicyError
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.types import Message
from swe_agent.prompts import templates as T
from swe_agent.schemas.state import (
    Plan,
)


@node("plan")
async def plan(state: AgentState, ctx: RunContext) -> dict[str, Any]:
    ws, _ = ctx.require_workspace()
    hs = state["hypotheses"]
    refs = [e for h in hs.hypotheses for e in h.evidence]
    snippets = render_evidence(ws, refs)
    ids = {h.id for h in hs.hypotheses}

    def validate(p: Plan) -> list[str]:
        errs: list[str] = []
        if p.hypothesis_id not in ids:
            errs.append(f"hypothesis_id must be one of {sorted(ids)}")
        planned = set(p.files_to_modify)
        kinds = {c.file: c.kind for c in p.changes}
        for c in p.changes:
            if c.file not in planned:
                errs.append(f"change for {c.file!r} is not listed in files_to_modify")
        for rel in p.files_to_modify:
            try:
                path = ws.jail.resolve(rel)
            except PathPolicyError as exc:
                errs.append(f"files_to_modify: {exc}")
                continue
            creating = kinds.get(rel) == "create"
            if creating and path.exists():
                errs.append(f"{rel!r} already exists; use kind='modify'")
            if not creating and not path.is_file():
                errs.append(f"{rel!r} does not exist (use kind='create' for new files)")
            try:
                IMPLEMENT_POLICY.check(rel, exists=path.exists())
            except PathPolicyError as exc:
                errs.append(f"files_to_modify: {exc}")
        return errs

    feedback = state.get("failure_feedback")
    p = await ctx.gateway.structured(
        ModelRole.REASONING,
        [
            Message(role="system", content=T.PLAN_SYSTEM),
            Message(role="user", content=T.plan_task(state["issue"], hs, snippets, feedback)),
        ],
        Plan,
        purpose="plan",
        validate=validate,
    )
    p.version = len(state.get("plan_history", [])) + 1
    return {"plan": p, "plan_history": [p]}
