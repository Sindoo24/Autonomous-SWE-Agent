"""Dashboard: task and run counts, and the most recent runs."""

from __future__ import annotations

import streamlit as st

from frontend.components.status import verdict_text
from frontend.session import client, go

RUN_LIMIT = 1000


def render() -> None:
    st.header("Dashboard")
    tasks = client().tasks(500)
    runs = client().runs(RUN_LIMIT)
    by_status: dict[str, int] = {}
    for r in runs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    c = st.columns(5)
    c[0].metric("Tasks", f"{len(tasks)}{'+' if len(tasks) == 500 else ''}")
    c[1].metric("Running", by_status.get("running", 0) + by_status.get("queued", 0))
    c[2].metric("Awaiting approval", by_status.get("awaiting_approval", 0))
    c[3].metric("Completed", by_status.get("finished", 0))
    c[4].metric("Failed", by_status.get("failed", 0))
    if len(runs) == RUN_LIMIT:
        st.caption(f"Run counts cover the latest {RUN_LIMIT} runs.")
    st.caption("Running includes queued runs. Completed = finished (approved or auto-approved).")

    st.subheader("Recent runs")
    if not runs:
        st.info("No runs yet. Start one from New task.")
        return
    recent = runs[:100]
    rows = [
        {
            "run": r["id"],
            "task": r["task_id"],
            "status": r["status"],
            "system": r["system"],
            "variant": r["variant"],
            "verdict": verdict_text(r),
            "termination": r["termination_reason"] or "-",
            "iterations": r["iterations"],
            "tool calls": r["tool_calls"],
            "created": (r["created_at"] or "")[:19],
        }
        for r in recent
    ]
    st.dataframe(rows, hide_index=True)
    pick = st.selectbox("Open run", [r["id"] for r in recent])
    if st.button("Open"):
        go("Run", pick)
        st.rerun()
