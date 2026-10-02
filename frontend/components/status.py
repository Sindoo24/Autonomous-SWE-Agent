"""Run status badges and the task / run detail header."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

LIVE = ("queued", "running")
STATUS_COLOR = {
    "queued": "gray",
    "running": "blue",
    "awaiting_approval": "orange",
    "finished": "green",
    "failed": "red",
    "cancelled": "gray",
}


def badge(status: str | None) -> str:
    return f":{STATUS_COLOR.get(status or '', 'gray')}[**{status or '-'}**]"


def verdict_text(run: dict[str, Any]) -> str:
    if run.get("verification_status") is None:
        return "-"
    return f"{run['verification_status']} L{run.get('verification_level')}"


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def elapsed_s(run: dict[str, Any], now: datetime | None = None) -> float | None:
    """Seconds since the run started; for a finished run, start to finish."""
    start = _ts(run.get("started_at"))
    if start is None:
        return None
    end = _ts(run.get("finished_at")) or now or datetime.now(UTC)
    return max(0.0, (end - start).total_seconds())


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "-"
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s" if m else f"{s:.1f}s"


def run_details(run: dict[str, Any], task: dict[str, Any] | None, status: dict[str, Any]) -> None:
    """Task ID, run ID, repository, issue, current node, status, iteration, elapsed time."""
    st.subheader(f"Run {run['id']}")
    c = st.columns(5)
    c[0].markdown(f"status<br>{badge(run['status'])}", unsafe_allow_html=True)
    # LangGraph's `next` is the node running or paused (e.g. human_approval); current_node is
    # the last node that finished.
    nxt = status.get("next") or []
    current = nxt[0] if nxt else status.get("current_node")
    c[1].markdown(f"current node<br>`{current or '-'}`", unsafe_allow_html=True)
    iteration = status.get("iteration", run.get("iterations"))
    c[2].metric("iteration", iteration if iteration is not None else "-")
    c[3].metric("elapsed", fmt_seconds(elapsed_s(run)))
    calls = run["tool_calls"]
    if calls is None:
        calls = (status.get("budget") or {}).get("used_tool_calls")
    c[4].metric("tool calls", calls if calls is not None else "-")
    st.caption(
        f"task `{run['task_id']}` · run `{run['id']}` · system `{run['system']}` "
        f"({run['variant']}) · termination `{run['termination_reason'] or '-'}`"
    )
    if task:
        st.markdown(f"**Repository:** `{task['repository']}`")
        with st.expander("Issue", expanded=False):
            st.text(task["issue"])
    if run.get("error"):
        st.error(run["error"])
