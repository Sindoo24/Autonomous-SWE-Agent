"""OpenTelemetry spans derived from the trajectory (JSONL exporter)."""

from __future__ import annotations

import json
from pathlib import Path

from swe_agent.agents.runner import run_task
from swe_agent.config import Settings
from swe_agent.llm.scripted import ScriptedProvider
from tests.integration.test_graph_e2e import (
    HYPOTHESES,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)


async def test_spans_form_one_trace_per_run(users_repo: Path, settings: Settings) -> None:
    settings.otel_exporter = "jsonl"
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    spans = [json.loads(x) for x in (out.artifacts_dir / "spans.jsonl").read_text().splitlines()]
    by_id = {s["span_id"]: s for s in spans}
    roots = [s for s in spans if s["name"] == "run"]
    assert len(roots) == 1 and roots[0]["parent_id"] is None
    root = roots[0]
    assert root["attributes"]["swe.run_id"] == out.run_id
    assert len({s["trace_id"] for s in spans}) == 1

    nodes = [s for s in spans if s["name"].startswith("node:")]
    assert [n["name"] for n in sorted(nodes, key=lambda s: s["start_ns"])][:3] == [
        "node:intake",
        "node:prepare_repo",
        "node:explore",
    ]
    assert all(n["parent_id"] == root["span_id"] for n in nodes)

    tools = [s for s in spans if s["name"].startswith("tool:")]
    models = [s for s in spans if s["name"].startswith("model:")]
    assert len(models) == 9 and {m["attributes"]["swe.role"] for m in models} == {
        "reasoning",
        "coder",
    }
    assert {t["name"] for t in tools} >= {"tool:read_file", "tool:edit_file"}
    for child in [*tools, *models]:
        parent = by_id[child["parent_id"]]
        assert parent["name"].startswith("node:")
        # children lie inside their node span (back-dated from recorded durations)
        assert parent["start_ns"] - 5_000_000 <= child["start_ns"] <= child["end_ns"]
        assert child["end_ns"] <= parent["end_ns"] + 5_000_000
    assert all(s["duration_ms"] >= 0 for s in spans)


async def test_exporter_none_writes_no_spans(users_repo: Path, settings: Settings) -> None:
    settings.otel_exporter = "none"
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert not (out.artifacts_dir / "spans.jsonl").exists()
    assert out.report["termination_reason"] == "patch_proposed"
