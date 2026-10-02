"""Create a task (repository + issue) and start a run for it."""

from __future__ import annotations

import streamlit as st

from frontend.api.client import ApiError
from frontend.session import client, go

SYSTEMS = {
    "Agent (closed loop)": "agent",
    "Baseline B: ReAct": "baseline_react",
    "Baseline A: single-shot": "baseline_single_shot",
}


def render() -> None:
    st.header("New task")
    st.caption("The agent works on an isolated clone; your repository is never modified.")
    with st.form("new"):
        repo = st.text_input("Repository", placeholder="/path/to/repo or https://github.com/...")
        issue = st.text_area(
            "Issue", height=160, placeholder="Describe the bug: what happens, what should happen."
        )
        c1, c2 = st.columns(2)
        system = SYSTEMS[c1.selectbox("System", list(SYSTEMS))]
        approval = c2.selectbox(
            "Approval",
            ["manual", "auto"],
            help="manual: the run pauses for your approval before exporting the patch",
        )
        with st.expander("Configuration"):
            reproduce = st.checkbox("Write a reproduction test first", value=True)
            iterations = st.number_input("Max iterations", 1, 10, 5)
        submitted = st.form_submit_button("Start run", type="primary")
    if submitted:
        if not repo.strip() or len(issue.strip()) < 10:
            st.error("Give a repository and an issue of at least 10 characters.")
            return
        overrides: dict[str, dict[str, object]] = {"budget": {"max_iterations": int(iterations)}}
        if system == "agent":
            overrides["agent"] = {"reproduce": reproduce}
        try:
            task = client().create_task(repo.strip(), issue.strip())
            run = client().create_run(task, system=system, approval=approval, overrides=overrides)
        except ApiError as exc:
            st.error(str(exc))
            return
        go("Run", run)
        st.rerun()
