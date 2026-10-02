"""Smoke test against a real model endpoint. Skipped unless SWE_LIVE_MODEL=1.

Run (Ollama on the host):
    ollama pull qwen2.5-coder:7b
    SWE_LIVE_MODEL=1 SWE_LIVE_ENV=configs/models/ollama-small.env pytest -m live_model -s

Needs Docker too (the sandbox runs the repository's tests) unless SANDBOX_ENABLED=false.
This asserts the run completes with a well-formed report. It does not assert the bug gets
fixed: that is what `swe-agent bench run` measures, with held-out tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from swe_agent.agents.runner import run_task
from swe_agent.config import load_settings
from swe_agent.observability.logging import configure_logging
from swe_agent.schemas.state import TerminationReason

pytestmark = [
    pytest.mark.live_model,
    pytest.mark.skipif(
        os.environ.get("SWE_LIVE_MODEL") != "1", reason="set SWE_LIVE_MODEL=1 to run"
    ),
]

ISSUE = "POST /users returns HTTP 500 when the email field is missing. It should return 422."


async def test_live_run_produces_report(users_repo: Path, tmp_path: Path) -> None:
    settings = load_settings(os.environ.get("SWE_LIVE_ENV"))
    settings.workspace_root = tmp_path / "ws"
    settings.artifacts_root = tmp_path / "art"
    settings.checkpoint_db = tmp_path / "checkpoints.sqlite"
    settings.agent.approval = "auto"  # nothing to approve in a smoke test
    configure_logging("INFO", json=False)
    out = await run_task(str(users_repo), ISSUE, settings)
    r = out.report
    print("\n--- live run report ---")
    for key in ("termination_reason", "status", "files_changed", "root_cause", "latency_s"):
        print(f"{key}: {r.get(key)}")
    print(f"trace: {r.get('trace')}")
    print(f"artifacts: {out.artifacts_dir}")
    # The verdict may legitimately be either: users_service has no visible test covering the
    # bug, so NOT VERIFIED (level 1) is the honest best when no reproduction test is written.
    assert r["status"] in {"VERIFIED", "NOT VERIFIED"}
    # Derived from the enum so this list cannot go stale as termination reasons are added.
    assert r["termination_reason"] in {t.value for t in TerminationReason}
    assert r["trace"]["model_calls"] > 0
