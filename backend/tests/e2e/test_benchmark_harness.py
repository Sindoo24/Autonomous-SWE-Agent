"""Benchmark harness wiring (Docker). Uses the reference fix and a scripted model, so these
tests validate the harness and the tasks, not any model's performance."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_agent.config import SandboxSettings, Settings
from swe_agent.evaluation.benchmark.evaluate import evaluate_patch, validate_task
from swe_agent.evaluation.benchmark.run import run_benchmark, summarize
from swe_agent.evaluation.benchmark.tasks import load_tasks, materialize
from swe_agent.llm.scripted import ScriptedProvider
from tests.conftest import git
from tests.integration.test_graph_e2e import (
    HYPOTHESES,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = pytest.mark.docker


def test_tasks_load_and_materialize(tmp_path: Path) -> None:
    tasks = {t.id: t for t in load_tasks()}
    assert len(tasks) == 25
    cats: dict[str, int] = {}
    for t in tasks.values():
        cats[t.category] = cats.get(t.category, 0) + 1
    # >= 3 tasks per category (README: Benchmark)
    assert set(cats) == {"validation_bug", "regression_bug", "edge_case", "data_processing_bug",
                         "algorithmic_bug", "logic_bug", "api_bug"}  # fmt: skip
    assert min(cats.values()) >= 3, cats
    diff = [t.difficulty for t in tasks.values()]
    assert (diff.count("easy"), diff.count("medium"), diff.count("hard")) == (10, 10, 5)
    squashed = materialize(tasks["users-missing-email"], tmp_path / "a")
    assert git(squashed, "rev-list", "--count", "HEAD").strip() == "1"
    assert 'normalize_email(payload["email"])' in (squashed / "users_service/api.py").read_text()
    regression = materialize(tasks["datakit-slugify-digits"], tmp_path / "b")
    log = git(regression, "log", "--format=%s")
    assert log.splitlines()[0].startswith("Simplify slugify")
    assert not list(squashed.glob("tests_heldout*"))  # held-out tests never enter the repo


def test_reference_fix_scores_and_no_patch_fails(sandbox_settings: SandboxSettings) -> None:
    task = next(t for t in load_tasks() if t.id == "datakit-merge-touching")
    reference = task.bug_patch.read_text()
    fix = (
        "\n".join(
            (
                ("+" + ln[1:])
                if ln.startswith("-") and not ln.startswith("---")
                else ("-" + ln[1:])
                if ln.startswith("+") and not ln.startswith("+++")
                else ln
            )
            for ln in reference.splitlines()
        )
        + "\n"
    )
    ok = evaluate_patch(task, fix, sandbox_settings)
    assert ok.success and ok.heldout_passed == ok.heldout_total == 3
    none = evaluate_patch(task, None, sandbox_settings)
    assert not none.success and none.error == "no patch"
    junk = evaluate_patch(task, "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", sandbox_settings)
    assert not junk.success and not junk.patch_applied


def test_validate_task(sandbox_settings: SandboxSettings) -> None:
    task = next(t for t in load_tasks() if t.id == "datakit-slugify-digits")
    v = validate_task(task, sandbox_settings)
    assert v.valid, v.problems
    assert v.visible_failing_at_base == ["tests/test_text.py::test_slugify_keeps_digits"]


async def test_scripted_provider_refused(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scripted"):
        await run_benchmark(settings, tmp_path, provider=ScriptedProvider([]))


async def test_harness_end_to_end_with_scripted_agent(
    settings: Settings, sandbox_settings: SandboxSettings, tmp_path: Path
) -> None:
    settings.sandbox = sandbox_settings.model_copy(update={"enabled": True})
    from tests.e2e.test_graph_execution import REPRO

    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, REPRO, *implement_script()])
    rows = await run_benchmark(
        settings,
        tmp_path / "bench",
        task_ids=["users-missing-email"],
        provider=provider,
        allow_scripted=True,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["success_l5"] is True and row["heldout_passed"] == 3
    # the reproduction test supplies the fail -> pass evidence the visible suite lacks
    assert row["agent_status"] == "VERIFIED" and row["agent_level"] == 4
    assert row["reproduced"] is True
    assert (tmp_path / "bench" / "results.jsonl").read_text().count("\n") == 1
    s = summarize(rows)
    assert s["success_rate"] == 1.0 and s["by_task"] == {"users-missing-email": [True]}
