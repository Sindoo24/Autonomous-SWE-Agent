"""Changed files, the diff, and every patch attempt."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)


def changed_files(diff: str) -> list[str]:
    return [m.group(2) for m in _FILE.finditer(diff)]


def render(diff: str, patches: list[dict[str, Any]] | None) -> None:
    files = changed_files(diff)
    st.markdown("**Changed files:** " + (", ".join(f"`{f}`" for f in files) if files else "none"))
    st.code(diff or "(no changes yet)", language="diff")
    if not patches:
        return
    st.markdown("**Patch attempts**")
    for p in patches:
        v = p.get("validation") or {}
        ok = v.get("accepted", p.get("accepted"))
        title = (
            f"iteration {p.get('iteration')} · attempt {p.get('attempt')} · "
            f"{'accepted' if ok else 'rejected'} · {', '.join(p.get('files_changed') or [])}"
        )
        with st.expander(title, expanded=False):
            for err in v.get("errors") or p.get("errors") or []:
                st.error(err)
            if p.get("diff"):
                st.code(p["diff"], language="diff")
