"""Exploration findings, hypotheses, the structured plan and re-plans."""

from __future__ import annotations

from typing import Any

import streamlit as st


def render(plan: dict[str, Any] | None) -> None:
    if not plan:
        st.info("No plan yet.")
        return
    if plan.get("findings"):
        st.markdown("**Exploration findings**")
        st.write(plan["findings"].get("summary"))
    for h in plan.get("hypotheses") or []:
        st.markdown(f"**{h['id']}** ({h.get('confidence')}): {h['statement']}")
    current = plan.get("plan")
    if current:
        st.markdown("**Plan**")
        if isinstance(current, dict):
            st.caption(
                f"version {current.get('version')} · hypothesis {current.get('hypothesis_id')}"
            )
            st.write(current.get("problem"))
            files = current.get("files_to_modify") or []
            st.markdown("**Files to modify:** " + ", ".join(f"`{f}`" for f in files))
            if current.get("changes"):
                st.dataframe(current["changes"], hide_index=True)
            if current.get("tests_to_run"):
                st.markdown("**Tests to run:** " + ", ".join(current["tests_to_run"]))
            if current.get("risks"):
                st.markdown("**Risks:** " + "; ".join(current["risks"]))
            if current.get("rollback_strategy"):
                st.markdown(f"**Rollback:** {current['rollback_strategy']}")
        with st.expander("Plan (raw)"):
            st.json(current, expanded=False)
    for i, p in enumerate(plan.get("plan_history") or [], 1):
        with st.expander(f"plan version {i}"):
            st.json(p)
    for rc in plan.get("root_cause_analyses") or []:
        st.warning(
            f"Root-cause analysis: {rc['root_cause']} "
            f"(fix in right place: {rc['fix_in_right_place']})"
        )
