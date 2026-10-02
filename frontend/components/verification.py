"""VERIFIED / NOT VERIFIED, with the verification level and its evidence."""

from __future__ import annotations

from typing import Any

import streamlit as st


def render(verification: dict[str, Any] | None) -> None:
    if not verification:
        st.info("Not verified yet: verification runs after the tests pass.")
        return
    level = verification.get("level")
    if verification.get("status") == "VERIFIED":
        st.success(f"VERIFIED (level {level})")
    else:
        st.error(f"NOT VERIFIED (level {level})")
    for line in verification.get("evidence") or []:
        st.markdown(f"- {line}")
    for line in verification.get("notes") or []:
        st.caption(line)
