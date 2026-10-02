"""Agent progress: the LangGraph stages, which have run, and the node timeline."""

from __future__ import annotations

from typing import Any

import streamlit as st

# The nodes of the agent graph (swe_agent/agents/graph.py) in pipeline order. Loops go back to
# plan / implement / explore; report_failure is the failure exit. Kept in sync by
# tests/unit/test_frontend_stages.py.
STAGES = [
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
    "verify",
    "human_approval",
    "finalize",
    "report_failure",
]
_ICON = {"done": "✅", "running": "🔄", "failed": "❌", "pending": "⬜", "skipped": "➖"}


def timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per node execution, from node_started / node_finished events."""
    rows: list[dict[str, Any]] = []
    for e in events:
        if e["event"] == "node_started":
            rows.append(
                {
                    "node": e["node"],
                    "iteration": e["iteration"],
                    "status": "running",
                    "ms": None,
                    "seq": e["seq"],
                }
            )
        elif e["event"] == "node_finished" and rows and rows[-1]["node"] == e["node"]:
            rows[-1]["status"] = e["data"].get("status")
            rows[-1]["ms"] = e["data"].get("duration_ms")
    return rows


def stage_states(rows: list[dict[str, Any]], current: str | None) -> dict[str, str]:
    """done / running / failed / pending per stage, from the latest execution of each node."""
    out = dict.fromkeys(STAGES, "pending")
    for r in rows:
        if r["node"] in out:
            ok = r["status"] in ("ok", "success", "finished", None)
            out[r["node"]] = "running" if r["status"] == "running" else "done" if ok else "failed"
    if current in out and out[current] == "pending":
        out[current] = "running"
    return out


def render(status: dict[str, Any], events: list[dict[str, Any]]) -> None:
    rows = timeline(events)
    nxt = status.get("next") or []
    states = stage_states(rows, nxt[0] if nxt else status.get("current_node"))
    cols = st.columns(5)
    for i, name in enumerate(STAGES):
        cols[i % 5].markdown(f"{_ICON[states[name]]} `{name}`")
    if status.get("current_node"):
        st.caption(f"last finished node: **{status['current_node']}**  ·  next: {nxt}")
    budget = status.get("budget") or {}
    if budget:
        b1, b2 = st.columns(2)
        b1.progress(
            min(1.0, budget["used_tool_calls"] / max(1, budget["max_tool_calls"])),
            text=f"tool calls {budget['used_tool_calls']}/{budget['max_tool_calls']}",
        )
        b2.progress(
            min(1.0, budget["used_iterations"] / max(1, budget["max_iterations"])),
            text=f"iterations {budget['used_iterations']}/{budget['max_iterations']}",
        )
    st.dataframe(rows, hide_index=True)
    for e in events:
        if e["event"] in ("recovery", "approval"):
            st.caption(f"#{e['seq']} {e['event']}: {str(e['data'])[:300]}")
