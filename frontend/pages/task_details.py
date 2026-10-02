"""Task / run details: live progress, plan, tool trace, tests, patch, verification, approval."""

from __future__ import annotations

import streamlit as st

from frontend.components import (
    agent_progress,
    approval,
    patch_view,
    plan_view,
    test_results,
    tool_trace,
    verification,
)
from frontend.components.status import LIVE, run_details
from frontend.session import client


def render() -> None:
    api = client()
    runs = api.runs(100)
    ids = [r["id"] for r in runs]
    if not ids:
        st.info("No runs yet.")
        return
    run_id = st.session_state.get("run_id")
    run_id = st.selectbox("Run", ids, index=ids.index(run_id) if run_id in ids else 0)
    st.session_state["run_id"] = run_id
    first = api.run(run_id)
    task = api.task(first["task_id"])
    live = first["status"] in LIVE

    @st.fragment(run_every=2 if live else None)
    def live_part() -> None:
        run = api.run(run_id)
        status = api.status(run_id)
        run_details(run, task, status)
        if run["status"] in (*LIVE, "awaiting_approval") and st.button("Cancel run"):
            api.cancel(run_id)
        st.markdown("#### Agent progress")
        agent_progress.render(status, api.trajectory(run_id)["events"])
        if live and run["status"] not in LIVE:
            st.rerun()  # finished or paused: refresh the whole page once

    live_part()
    if api.run(run_id)["status"] == "awaiting_approval":
        approval.render(api, run_id, api.approval(run_id))

    tests = api.tests(run_id)
    tabs = st.tabs(["Plan", "Tool trace", "Tests", "Patch", "Verification", "Metrics", "Report"])
    with tabs[0]:
        plan_view.render(api.plan(run_id))
    with tabs[1]:
        tool_trace.render(api.trajectory(run_id)["events"])
    with tabs[2]:
        test_results.render(tests, lambda art: api.artifact(run_id, art))
    with tabs[3]:
        patch_view.render(api.diff(run_id), api.patches(run_id))
    with tabs[4]:
        verification.render((tests or {}).get("verification"))
    with tabs[5]:
        _metrics(run_id)
    with tabs[6]:
        report = api.report(run_id)
        if report:
            st.json(report, expanded=False)
        else:
            st.info("No report yet.")


def _metrics(run_id: str) -> None:
    m = client().metrics(run_id)
    if not m or not m.get("latency"):
        st.info("No metrics yet.")
        return
    lat, tok = m["latency"], m.get("tokens") or {}
    c = st.columns(5)
    for col, key in zip(
        c, ("active_ms", "model_ms", "tool_ms", "sandbox_ms", "other_ms"), strict=True
    ):
        col.metric(key.replace("_ms", ""), f"{(lat.get(key) or 0) / 1000:.1f}s")
    st.caption(
        f"model calls {tok.get('model_calls')} · tokens in {tok.get('tokens_in')} · "
        f"out {tok.get('tokens_out')} (None = the provider did not report usage)"
    )
    st.dataframe(m.get("tools") or [], hide_index=True)
    st.dataframe(m.get("nodes") or [], hide_index=True)
