from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_agent.agents.nodes.common import UNCANCELLABLE
from swe_agent.agents.runner import run_task
from swe_agent.api.deps import validate_source
from swe_agent.config import OverrideError, Settings, apply_overrides, check_overrides
from swe_agent.db.engine import is_postgres, libpq_url, sqlalchemy_url
from swe_agent.db.store import summary_from_report
from swe_agent.evaluation.config import VARIANTS, ExperimentConfig, SystemSpec
from swe_agent.evaluation.report import CostAssumptions, render_markdown
from swe_agent.llm.scripted import ScriptedProvider
from swe_agent.tools.registry import NODE_TOOLS
from tests.conftest import REPO_ROOT


def test_urls() -> None:
    assert sqlalchemy_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert sqlalchemy_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert libpq_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert (
        is_postgres("postgresql://x") and not is_postgres(None) and not is_postgres("sqlite:///x")
    )


def test_overrides(settings: Settings) -> None:
    out = apply_overrides(
        settings,
        {"agent": {"reproduce": False}, "budget": {"max_iterations": 1}, "model": {"seed": 3}},
    )
    assert out.agent.reproduce is False and out.budget.max_iterations == 1 and out.model.seed == 3
    assert settings.agent.reproduce is True  # original untouched
    for bad in (
        {"model": {"base_url": "x"}},
        {"sandbox": {"enabled": False}},
        {"agent": {"acceptance_dir": "/etc"}},
        {"nope": {}},
    ):
        with pytest.raises(OverrideError):
            check_overrides(bad)
    with pytest.raises(ValueError):
        apply_overrides(settings, {"agent": {"failure_routing": "random"}})


def test_validate_source(settings: Settings, tmp_path: Path) -> None:
    from fastapi import HTTPException

    settings.api_repo_roots = str(tmp_path)
    (tmp_path / "repo").mkdir()
    assert validate_source(str(tmp_path / "repo"), settings) == str((tmp_path / "repo").resolve())
    assert validate_source("https://example.com/a.git", settings).startswith("https://")
    for bad, code in (
        ("/", 403),
        (str(tmp_path / "repo" / ".." / ".."), 403),
        ("ext::sh -c x", 422),
        ("-oProxyCommand=x", 422),
    ):
        with pytest.raises(HTTPException) as e:
            validate_source(bad, settings)
        assert e.value.status_code == code, bad


def test_summary_from_report_never_invents_tokens() -> None:
    s = summary_from_report(
        {
            "trace": {"tokens_in": None, "tool_calls": 3},
            "verification": {"level": 2},
            "latency_s": 1.5,
        }
    )
    assert s["total_tokens_in"] is None and s["total_tool_calls"] == 3 and s["wall_ms"] == 1500


def test_experiment_config() -> None:
    cfg = ExperimentConfig.model_validate(
        {
            "name": "x",
            "repeats": 2,
            "seeds": [1, 2],
            "systems": [
                {"system": "agent"},
                {"system": "agent", "variant": "no_reproduce"},
                {"system": "baseline_react"},
            ],
        }
    )
    assert cfg.systems[1].effective_overrides() == {"agent": {"reproduce": False}}
    assert cfg.seed_for(1, 7) == 2 and cfg.systems[1].label == "agent:no_reproduce"
    with pytest.raises(ValueError, match="one entry per repeat"):
        ExperimentConfig.model_validate(
            {"name": "x", "repeats": 3, "seeds": [1], "systems": [{"system": "agent"}]}
        )
    with pytest.raises(ValueError, match="duplicate"):
        ExperimentConfig.model_validate({"name": "x", "systems": [{"system": "agent"}] * 2})
    with pytest.raises(ValueError, match="unknown agent variant"):
        SystemSpec(system="agent", variant="magic")
    with pytest.raises(ValueError, match="agent only"):
        SystemSpec(system="baseline_react", variant="no_reproduce")
    for name in VARIANTS:  # every shipped variant is a valid override set
        check_overrides(SystemSpec(system="agent", variant=name).effective_overrides())


def test_shipped_experiment_configs_parse() -> None:
    from swe_agent.evaluation.config import load_experiment

    paths = sorted((REPO_ROOT / "configs/experiments").glob("*.toml"))
    assert paths
    for p in paths:
        load_experiment(p)


def test_react_tools_and_cancel_rules() -> None:
    assert "run_tests" in NODE_TOOLS["react"]
    assert "run_tests" not in NODE_TOOLS["implement"] and "run_tests" not in NODE_TOOLS["explore"]
    assert {"finalize", "report_failure"} == UNCANCELLABLE


async def test_cancel_stops_before_the_next_node(users_repo: Path, settings: Settings) -> None:
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # intake and prepare_repo run, explore is cancelled

    out = await run_task(
        str(users_repo), "x" * 20, settings, provider=ScriptedProvider([]), cancel=cancel
    )
    r = out.report
    assert r["termination_reason"] == "cancelled"
    assert r["errors"][-1]["node"] == "explore" and r["errors"][-1]["kind"] == "cancelled"


def _tables(scripted: bool) -> dict[str, Any]:
    row = {
        "system": "agent",
        "variant": "full",
        "runs": 2,
        "evaluated": 2,
        "successes": 1,
        "success_rate": 0.5,
        "first_attempt_success_rate": 0.5,
        "final_test_pass_rate": 0.75,
        "recovery_rate": None,
        "mean_iterations": 1.5,
        "mean_iterations_successful": 1.0,
        "wall_ms_p50": 60000,
        "wall_ms_p90": 90000,
        "mean_model_ms": 1000.0,
        "mean_tool_ms": 10.0,
        "mean_sandbox_ms": 5000.0,
        "mean_tokens_in": 1000.0,
        "mean_tokens_out": 100.0,
        "total_tokens_in": 2000,
        "total_tokens_out": 200,
        "runs_without_token_data": 0,
        "total_active_ms": 3_600_000,
        "mean_tool_calls": 9.0,
        "mean_repeated_calls": 0.5,
        "mean_uncited_reads": 1.0,
        "plan_deviation_rate": 0.0,
        "tamper_attempts": 0,
    }
    return {
        "experiment": {
            "id": "exp_1",
            "name": "t",
            "status": "finished",
            "created_at": "now",
            "config": {
                "scripted": scripted,
                "task_ids": ["a", "b"],
                "repeats": 1,
                "model": {"provider": "ollama"},
            },
        },
        "summary": [row],
        "repeat_success": [],
        "by_difficulty": [],
        "by_category": [],
        "terminations": [],
        "failure_categories": [],
        "tool_usage": [],
        "per_task": [
            {
                "benchmark_task": "a",
                "difficulty": "easy",
                "category": "c",
                "system": "agent",
                "variant": "full",
                "runs": 1,
                "successes": 1,
            }
        ],
    }


def test_report_rendering() -> None:
    md = render_markdown(_tables(False), CostAssumptions(1.0, 2.0, 0.5))
    assert "1/2 (50%)" in md and "WARNING" not in md
    assert "one task is 50 percentage points" in md
    assert "$0.00" in md  # 2000 in + 200 out tokens at $1/$2 per M
    assert "$0.50" in md  # 1 GPU-hour at $0.5
    assert "| a | easy, c | 1/1 |" in md
    assert "WARNING" in render_markdown(_tables(True), CostAssumptions())
