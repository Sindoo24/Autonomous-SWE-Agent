"""The UI's stage list matches the agent graph's nodes (the UI must not import the agent)."""

from __future__ import annotations

import pytest

from swe_agent.agents.graph import build_graph

pytest.importorskip("streamlit", reason="UI extra not installed: pip install -e '.[ui]'")


def test_progress_stages_are_the_graph_nodes() -> None:
    from frontend.components.agent_progress import STAGES

    nodes = set(build_graph(execution=True).get_graph().nodes) - {"__start__", "__end__"}
    assert set(STAGES) == nodes
    assert len(STAGES) == len(nodes)


def test_stage_states_and_helpers() -> None:
    from frontend.components.agent_progress import stage_states, timeline
    from frontend.components.patch_view import changed_files
    from frontend.components.status import elapsed_s

    events = [
        {"event": "node_started", "node": "intake", "iteration": None, "seq": 1, "data": {}},
        {"event": "node_finished", "node": "intake", "iteration": None, "seq": 2,
         "data": {"status": "ok", "duration_ms": 5}},
        {"event": "node_started", "node": "explore", "iteration": None, "seq": 3, "data": {}},
    ]  # fmt: skip
    states = stage_states(timeline(events), "explore")
    assert states["intake"] == "done" and states["explore"] == "running"
    assert states["plan"] == "pending"
    diff = "diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
    assert changed_files(diff) == ["pkg/a.py"]
    run = {"started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:01:30+00:00"}
    assert elapsed_s(run) == 90.0
    assert elapsed_s({"started_at": None}) is None
