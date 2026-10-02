"""Experiment runner, baselines, SQL metrics and report, with a scripted model.

These tests check the evaluation harness end to end (Docker + PostgreSQL). Scripted runs are
flagged as such everywhere; nothing here is model performance.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from swe_agent.config import SandboxSettings, Settings
from swe_agent.db.engine import make_async_engine, session_factory
from swe_agent.evaluation.config import ExperimentConfig
from swe_agent.evaluation.metrics import experiment_tables
from swe_agent.evaluation.report import CostAssumptions, generate_report
from swe_agent.evaluation.runner import ExperimentRunner, run_experiment
from swe_agent.llm.scripted import ScriptedProvider, action
from swe_agent.worker.service import Worker
from tests.e2e.test_graph_execution import REPRO
from tests.integration.test_graph_e2e import (
    API,
    FIX_REPLACE,
    FIX_SEARCH,
    HYPOTHESES,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = [pytest.mark.docker, pytest.mark.postgres]

TASK = "users-missing-email"
SINGLE_SHOT = {
    "explanation": "validate email presence before normalising",
    "edits": [{"path": API, "search": FIX_SEARCH, "replace": FIX_REPLACE}],
}
REACT = [
    action("search_code", pattern="normalize_email"),
    action("read_file", path=API, start_line=20, end_line=32),
    action("edit_file", path=API, search=FIX_SEARCH, replace=FIX_REPLACE),
    action("run_tests"),
    action("finish", summary="missing email now raises ValidationError -> 422"),
]


def agent_script() -> list[Any]:
    return [*explore_script(), HYPOTHESES, PLAN, REPRO, *implement_script()]


class Scripts:
    def __init__(self, *scripts: list[Any]) -> None:
        self.queue = [[], *scripts]  # the first provider is the runner's scripted-ness probe
        self.used: list[ScriptedProvider] = []

    def __call__(self, _model: Any) -> ScriptedProvider:
        p = ScriptedProvider(self.queue.pop(0) if self.queue else [])
        self.used.append(p)
        return p


@pytest.fixture
def exp_settings(db_settings: Settings, sandbox_settings: SandboxSettings) -> Settings:
    db_settings.sandbox = sandbox_settings.model_copy(update={"enabled": True})
    return db_settings


async def _tables(settings: Settings, exp_id: str) -> dict[str, Any]:
    engine = make_async_engine(settings.database_url or "")
    try:
        async with session_factory(engine)() as s:
            data = await experiment_tables(s, exp_id)
            assert data is not None
            return data
    finally:
        await engine.dispose()


async def test_scripted_provider_is_refused(exp_settings: Settings, tmp_path: Path) -> None:
    cfg = ExperimentConfig(name="x", tasks=[TASK], systems=[{"system": "agent"}])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="scripted"):
        await run_experiment(exp_settings, cfg, out_dir=tmp_path, provider_factory=Scripts())


async def test_experiment_all_systems_end_to_end(exp_settings: Settings, tmp_path: Path) -> None:
    cfg = ExperimentConfig.model_validate(
        {
            "name": "harness-check",
            "tasks": [TASK],
            "repeats": 1,
            "seeds": [11],
            "systems": [
                {"system": "agent"},
                {"system": "baseline_single_shot"},
                {"system": "baseline_react"},
            ],
        }
    )
    scripts = Scripts(agent_script(), [SINGLE_SHOT], REACT)
    exp_id = await run_experiment(
        exp_settings, cfg, out_dir=tmp_path, provider_factory=scripts, allow_scripted=True
    )
    assert all(not p.script for p in scripts.used)  # every scripted response was consumed

    t = await _tables(exp_settings, exp_id)
    assert t["experiment"]["status"] == "finished" and t["experiment"]["config"]["scripted"]
    summary = {(r["system"], r["variant"]): r for r in t["summary"]}
    assert set(summary) == {
        ("agent", "full"),
        ("baseline_single_shot", "full"),
        ("baseline_react", "full"),
    }
    for row in summary.values():
        assert row["runs"] == 1 and row["evaluated"] == 1 and row["successes"] == 1, row
        assert row["final_test_pass_rate"] == 1.0 and row["first_attempt_success_rate"] == 1.0
        assert row["tamper_attempts"] == 0
    runs = {r["system"]: r for r in t["runs"]}
    assert runs["agent"]["verification_level"] == 4 and runs["agent"]["seed"] == 11
    assert runs["baseline_single_shot"]["verification_level"] == 0
    assert runs["baseline_react"]["total_tool_calls"] == 4  # finish is not a model tool call
    assert all(r["status"] == "finished" for r in t["runs"])
    assert summary[("agent", "full")]["plan_deviation_rate"] == 0.0
    assert summary[("baseline_react", "full")]["plan_deviation_rate"] is None  # no plan

    # the ReAct baseline ran the tests itself, through the sandbox
    engine = make_async_engine(exp_settings.database_url or "")
    async with session_factory(engine)() as s:
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM v_sandbox_execs x JOIN agent_runs r ON r.id = x.run_id "
                    "WHERE r.system = 'baseline_react' AND x.op = 'target'"
                )
            )
        ).scalar()
    await engine.dispose()
    assert n == 1

    out = generate_report(t, tmp_path / "report", CostAssumptions())
    md = out.read_text()
    assert "WARNING: these runs used a scripted" in md
    assert "| baseline_react |" in md and "1/1 (100%)" in md
    assert (tmp_path / "report" / "summary.csv").exists()
    has_mpl = importlib.util.find_spec("matplotlib") is not None  # optional [charts] extra
    assert (tmp_path / "report" / "success_rate.png").exists() == has_mpl
    assert (tmp_path / exp_id / "results.jsonl").read_text().count("\n") == 3

    # resuming the finished experiment runs nothing new
    again = await run_experiment(
        exp_settings,
        cfg,
        experiment_id=exp_id,
        out_dir=tmp_path,
        provider_factory=Scripts(),
        allow_scripted=True,
    )
    assert again == exp_id
    assert len((await _tables(exp_settings, exp_id))["runs"]) == 3


async def test_enqueue_only_then_workers_then_evaluate(
    exp_settings: Settings, tmp_path: Path
) -> None:
    cfg = ExperimentConfig.model_validate(
        {
            "name": "queued",
            "tasks": [TASK],
            "systems": [{"system": "baseline_single_shot"}],
        }
    )
    scripts = Scripts([SINGLE_SHOT])
    runner = ExperimentRunner(
        exp_settings, provider_factory=scripts, allow_scripted=True, out_dir=tmp_path
    )
    try:
        exp_id = await runner.run(cfg, execute=False)
        assert (await _tables(exp_settings, exp_id))["runs"][0]["status"] == "queued"
        worker = Worker(exp_settings, provider_factory=scripts)
        assert await worker.run_once()
        await worker.aclose()
        assert await runner.evaluate_pending(exp_id) == 1
        assert await runner.evaluate_pending(exp_id) == 0
    finally:
        await runner.aclose()
    row = (await _tables(exp_settings, exp_id))["runs"][0]
    assert row["status"] == "finished" and row["success"] is True
