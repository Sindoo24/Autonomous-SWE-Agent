"""Test runs: command, pass/fail, output and duration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st


def _n(x: Any) -> Any:
    return len(x) if isinstance(x, list) else x


def render(tests: dict[str, Any] | None, fetch_log: Callable[[str], str | None]) -> None:
    """`fetch_log(artifact_id)` returns a test log's text (from the API) or None."""
    if not tests:
        st.info("No test runs yet.")
        return
    if tests.get("reproduction"):
        r = tests["reproduction"]
        st.markdown(f"Reproduction test: `{r.get('nodeid') or r.get('test')}`")
        if r.get("content"):
            st.code(r["content"], language="python")
    if tests.get("baseline"):
        b = tests["baseline"]
        st.caption(f"baseline: {_n(b.get('passing'))} passing, failing: {b.get('failing')}")
    runs = tests.get("test_runs") or []
    st.dataframe(
        [
            {
                "id": r["id"],
                "kind": r["kind"],
                "status": r["status"],
                "passed": _n(r["passed"]),
                "failed": _n(r["failed"]),
                "errors": _n(r["errors"]),
                "ms": r["duration_ms"],
            }
            for r in runs
        ],
        hide_index=True,
    )
    for r in runs:
        ok = r["status"] == "passed"
        with st.expander(f"{'PASS' if ok else 'FAIL'} · {r['kind']} · {r['id']}"):
            st.markdown("**Command**")
            st.code(" ".join(r.get("command") or []) or "-", language="bash")
            st.caption(
                f"exit code {r.get('exit_code')} · {r['duration_ms']} ms"
                + (" · timed out" if r.get("timed_out") else "")
                + (" · out of memory" if r.get("oom_killed") else "")
            )
            if r.get("collection_errors"):
                st.error("Collection errors: " + "; ".join(r["collection_errors"]))
            log_id = r.get("log_artifact_id") or r.get("log_artifact")
            st.markdown("**Output** (stdout and stderr, captured as one stream by the sandbox)")
            log = fetch_log(log_id) if log_id else None
            st.code(log[-20_000:] if log else "(no output recorded)", language="text")
    for f in tests.get("failure_history") or []:
        st.warning(f"{f['category']} → {f['route']}: {f['summary']}")
