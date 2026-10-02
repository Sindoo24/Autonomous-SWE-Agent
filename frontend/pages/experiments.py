"""Experiment results, read from the API's metric views. Nothing here is computed in the UI."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.session import client


def _label(r: dict[str, Any]) -> str:
    return r["system"] if r["variant"] == "full" else f"{r['system']}:{r['variant']}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def render() -> None:
    st.header("Experiments")
    exps = client().experiments()
    if not exps:
        st.info(
            "No experiments yet. Run `swe-agent experiment run configs/experiments/smoke.toml`."
        )
        return
    pick = st.selectbox(
        "Experiment",
        [e["id"] for e in exps],
        format_func=lambda i: next(
            f"{e['name']} ({i}, {e['runs']} runs, {e['status']})" for e in exps if e["id"] == i
        ),
    )
    t = client().experiment_metrics(pick)
    if (t["experiment"].get("config") or {}).get("scripted"):
        st.error(
            "Scripted (fake) model: these numbers test the harness, they are not model performance."
        )
    summary = t["summary"]
    st.dataframe(
        [
            {
                "system": _label(r),
                "runs": r["runs"],
                "scored": r["evaluated"],
                "success": _pct(r["success_rate"]),
                "first attempt": _pct(r["first_attempt_success_rate"]),
                "held-out pass": _pct(r["final_test_pass_rate"]),
                "recovery": _pct(r["recovery_rate"]),
                "iterations": r["mean_iterations"],
                "tool calls": r["mean_tool_calls"],
                "tokens in": r["mean_tokens_in"],
                "wall p50 (s)": (r["wall_ms_p50"] or 0) / 1000,
            }
            for r in summary
        ],
        hide_index=True,
    )
    if summary:
        data = [{"system": _label(r), "success %": 100 * (r["success_rate"] or 0)} for r in summary]
        st.bar_chart(data, x="system", y="success %", horizontal=True)
    st.markdown("**Per task**")
    st.dataframe(t["per_task"], hide_index=True)
    st.markdown("**Termination reasons**")
    st.dataframe(t["terminations"], hide_index=True)
