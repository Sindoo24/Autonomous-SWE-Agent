"""Tool calls and model calls from the run's trajectory."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st


def tool_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = [e for e in events if e["event"] == "tool_call" and not e["data"].get("system")]
    return [
        {
            "seq": e["seq"],
            "node": e["node"],
            "iteration": e["iteration"],
            "tool": e["data"]["tool"],
            "status": e["data"].get("status"),
            "ms": e["data"].get("duration_ms"),
            "repeated": e["data"].get("repeated"),
            "args": json.dumps(e["data"].get("args"))[:160],
            "error": (e["data"].get("error") or "")[:160],
        }
        for e in calls
    ]


def render(events: list[dict[str, Any]]) -> None:
    rows = tool_rows(events)
    st.caption(f"{len(rows)} tool calls")
    st.dataframe(rows, hide_index=True)
    models = [e for e in events if e["event"] == "model_call"]
    st.caption(f"{len(models)} model calls")
    st.dataframe(
        [
            {
                "seq": e["seq"],
                "node": e["node"],
                "role": e["data"].get("role"),
                "purpose": e["data"].get("purpose"),
                "status": e["data"].get("status"),
                "tokens in": e["data"].get("tokens_in"),
                "tokens out": e["data"].get("tokens_out"),
                "ms": e["data"].get("latency_ms"),
            }
            for e in models
        ],
        hide_index=True,
    )
