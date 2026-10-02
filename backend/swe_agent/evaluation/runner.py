"""Experiment runner: systems x variants x benchmark tasks x repeats, recorded in
PostgreSQL and scored with held-out tests.

Each run is an ordinary API-style run (task + run + job rows), executed by the same Worker code
path the API uses, so experiments exercise exactly the production path. By default the runner
executes its own jobs one at a time; with `execute=False` it only enqueues them for
`swe-agent worker` processes and `evaluate_pending` scores them afterwards.

Order is interleaved (repeat -> task -> system) so slow drift (thermal throttling, a model
server restart) affects every system equally. Re-running with the same experiment id resumes:
combinations that already have a run are skipped.

A scripted provider is refused unless `allow_scripted=True` (tests); such experiments are
flagged in the database and the report says their numbers are not model performance.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from swe_agent.config import Settings
from swe_agent.core.ids import new_id
from swe_agent.db import store
from swe_agent.db.engine import DatabaseNotConfiguredError
from swe_agent.db.models import AgentRun, BenchmarkTaskRow, Evaluation, Experiment, Patch, Task
from swe_agent.evaluation.benchmark.evaluate import EvalResult, evaluate_patch
from swe_agent.evaluation.benchmark.tasks import BenchmarkTask, load_tasks, materialize
from swe_agent.evaluation.config import ExperimentConfig
from swe_agent.llm.factory import build_provider
from swe_agent.observability.logging import get_logger
from swe_agent.prompts.baselines import BASELINE_PROMPT_VERSION
from swe_agent.prompts.templates import PROMPT_VERSION
from swe_agent.worker.service import ProviderFactory, Worker, model_config_of

_log = get_logger("experiment")
TERMINAL = ("finished", "failed", "cancelled")


class SchemaMissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("database schema is missing or outdated: run `swe-agent db upgrade`")


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("swe-agent")
    except Exception:
        return "unknown"


class ExperimentRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_factory: ProviderFactory | None = None,
        allow_scripted: bool = False,
        root: Path | None = None,
        out_dir: Path = Path(".data/experiments"),
    ) -> None:
        if not settings.database_url:
            raise DatabaseNotConfiguredError()
        # Benchmark patches are scored on fresh copies and never applied anywhere: no approval.
        self.settings = settings.model_copy(deep=True)
        self.settings.agent.approval = "auto"
        if not self.settings.sandbox.enabled:
            raise ValueError("experiments need the sandbox (SANDBOX_ENABLED=true)")
        self.provider_factory = provider_factory or build_provider
        self.allow_scripted = allow_scripted
        self.root = root or settings.benchmarks_dir.resolve()
        self.out_dir = out_dir
        self.worker = Worker(self.settings, worker_id="experiment-runner",
                             provider_factory=self.provider_factory)  # fmt: skip
        self.sessions = self.worker.sessions

    async def aclose(self) -> None:
        await self.worker.aclose()

    # ------------------------------------------------------------------ setup

    async def _check_schema(self) -> None:
        async with self.sessions() as s:
            found = await s.execute(text("SELECT to_regclass('public.v_experiment_summary')"))
            if found.scalar() is None:
                raise SchemaMissingError()

    async def _scripted(self) -> bool:
        probe = self.provider_factory(self.settings.model)
        try:
            return probe.name == "scripted"
        finally:
            await probe.aclose()

    async def _sync_tasks(self, tasks: list[BenchmarkTask]) -> None:
        async with self.sessions() as s, s.begin():
            for t in tasks:
                values = {
                    "id": t.id, "repo": t.repo, "category": t.category,
                    "difficulty": t.difficulty, "issue_text": t.issue,
                    "expected_behavior": t.expected_behavior,
                    "heldout_total": t.heldout_test_count(),
                    "visible_failing": list(t.visible_failing),
                }  # fmt: skip
                stmt = insert(BenchmarkTaskRow).values(**values)
                changes = {k: v for k, v in values.items() if k != "id"}
                await s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=changes))

    # ------------------------------------------------------------------ run

    async def run(
        self, config: ExperimentConfig, *, experiment_id: str | None = None, execute: bool = True
    ) -> str:
        await self._check_schema()
        scripted = await self._scripted()
        if scripted and not self.allow_scripted:
            raise ValueError("refusing to run an experiment with a scripted provider")
        tasks = load_tasks(self.root, None if config.tasks == "all" else list(config.tasks))
        if config.tasks != "all":
            missing = sorted(set(config.tasks) - {t.id for t in tasks})
            if missing:
                raise ValueError(f"unknown benchmark tasks: {missing}")
        await self._sync_tasks(tasks)
        exp_id = await self._experiment(config, tasks, experiment_id, scripted)
        done = await self._existing(exp_id)
        exp_dir = (self.out_dir / exp_id).resolve()
        exp_dir.mkdir(parents=True, exist_ok=True)
        total = config.repeats * len(tasks) * len(config.systems)
        n = 0
        for rep in range(config.repeats):
            for task in tasks:
                for spec in config.systems:
                    n += 1
                    key = (task.id, spec.system, spec.variant, rep)
                    if key in done:
                        continue
                    _log.info("experiment_run", experiment=exp_id, n=n, of=total, task=task.id,
                              system=spec.label, repeat=rep)  # fmt: skip
                    repo = materialize(task, exp_dir / "repos" /
                                       f"{task.id}--{spec.label.replace(':', '--')}--r{rep}",
                                       self.root)  # fmt: skip
                    overrides = spec.effective_overrides()
                    overrides.setdefault("agent", {})["approval"] = "auto"
                    seed = config.seed_for(rep, self.settings.model.seed)
                    if seed is not None:
                        overrides.setdefault("model", {})["seed"] = seed
                    async with self.sessions() as s, s.begin():
                        db_task = await store.create_task(s, str(repo), task.issue,
                                                          created_by="experiment",
                                                          benchmark_task_id=task.id)  # fmt: skip
                        run, _ = await store.create_run(
                            s, db_task.id, system=spec.system, variant=spec.variant,
                            overrides=overrides, seed=seed, experiment_id=exp_id, repeat=rep,
                        )  # fmt: skip
                        job = await store.active_job(s, run.id)
                    if execute and job is not None:
                        async with self.sessions() as s, s.begin():
                            claimed = await store.claim_specific(s, job.id, self.worker.id)
                        if claimed is not None:
                            await self.worker.process(claimed)
                        await self.evaluate_run(run.id, exp_dir)
        if execute:
            await self.evaluate_pending(exp_id)
            async with self.sessions() as s, s.begin():
                exp = await s.get(Experiment, exp_id)
                if exp is not None:
                    exp.status = "finished"
                    exp.finished_at = datetime.now(UTC)
        return exp_id

    async def _experiment(
        self,
        config: ExperimentConfig,
        tasks: list[BenchmarkTask],
        experiment_id: str | None,
        scripted: bool,
    ) -> str:
        async with self.sessions() as s, s.begin():
            if experiment_id:
                exp = await s.get(Experiment, experiment_id)
                if exp is None:
                    raise ValueError(f"no experiment {experiment_id}")
                exp.status = "running"
                return str(exp.id)
            exp_id = new_id("exp")
            sb = self.settings.sandbox
            s.add(Experiment(id=exp_id, name=config.name, status="running", config={
                **config.model_dump(mode="json"),
                "task_ids": [t.id for t in tasks],
                "model": model_config_of(self.settings),
                "budget": self.settings.budget.model_dump(),
                "prompt_version": PROMPT_VERSION,
                "baseline_prompt_version": BASELINE_PROMPT_VERSION,
                "agent_version": _version(),
                "sandbox": {"base_image": sb.base_image, "python": sb.python_version,
                            "memory": sb.memory, "cpus": sb.cpus},
                "scripted": scripted,
            }))  # fmt: skip
            return exp_id

    async def _existing(self, exp_id: str) -> set[tuple[str, str, str, int]]:
        async with self.sessions() as s:
            rows = await s.execute(
                select(Task.benchmark_task_id, AgentRun.system, AgentRun.variant, AgentRun.repeat)
                .join(Task, Task.id == AgentRun.task_id)
                .where(AgentRun.experiment_id == exp_id)
            )
            return {(r[0], r[1], r[2], r[3]) for r in rows}

    # ------------------------------------------------------------------ evaluation

    async def evaluate_pending(self, exp_id: str) -> int:
        """Score every finished run of the experiment that has no evaluation yet."""
        async with self.sessions() as s:
            rows = await s.execute(text(
                "SELECT r.id FROM agent_runs r LEFT JOIN evaluations e ON e.run_id = r.id "
                "WHERE r.experiment_id = :e AND e.run_id IS NULL "
                "AND r.status IN ('finished', 'failed', 'cancelled')"), {"e": exp_id})  # fmt: skip
            ids = [r[0] for r in rows]
        exp_dir = (self.out_dir / exp_id).resolve()
        for run_id in ids:
            await self.evaluate_run(run_id, exp_dir)
        return len(ids)

    async def evaluate_run(self, run_id: str, exp_dir: Path) -> EvalResult | None:
        async with self.sessions() as s:
            run = await store.get_run(s, run_id)
            task_row = await s.get(Task, run.task_id) if run else None
            q = select(Patch).where(Patch.run_id == run_id).order_by(Patch.seq)
            patches = list((await s.execute(q)).scalars())
        if run is None or task_row is None or task_row.benchmark_task_id is None:
            return None
        if run.status not in TERMINAL:
            return None
        task = load_tasks(self.root, [task_row.benchmark_task_id])[0]
        art = Path(run.artifacts_dir) if run.artifacts_dir else None
        final = art / "final.patch" if art else None
        patch = final.read_text(encoding="utf-8") if final and final.is_file() else None
        sb = self.settings.sandbox
        ev = await asyncio.to_thread(evaluate_patch, task, patch, sb, self.root)
        first: bool | None = ev.success
        iterations = run.iterations or 1
        if run.system == "agent" and iterations > 1:
            first_patch = next((p for p in patches if p.accepted and p.iteration <= 1), None)
            text_ = _artifact(art, first_patch.diff_artifact_id) if first_patch else None
            if text_ is None:
                first = False
            else:
                first = (
                    await asyncio.to_thread(evaluate_patch, task, text_, sb, self.root)
                ).success
        async with self.sessions() as s, s.begin():
            values = {
                "run_id": run_id, "benchmark_task_id": task.id,
                "patch_applied": ev.patch_applied, "heldout_total": ev.heldout_total,
                "heldout_passed": ev.heldout_passed, "heldout_failed": ev.heldout_failed,
                "visible_regressions": ev.visible_regressions, "success": ev.success,
                "first_patch_success": first, "error": ev.error,
            }  # fmt: skip
            stmt = insert(Evaluation).values(**values)
            await s.execute(stmt.on_conflict_do_update(
                index_elements=["run_id"],
                set_={k: v for k, v in values.items() if k != "run_id"}))  # fmt: skip
        row = {
            "experiment": run.experiment_id, "run_id": run_id, "task": task.id,
            "system": run.system, "variant": run.variant, "repeat": run.repeat, "seed": run.seed,
            "termination_reason": run.termination_reason, "agent_level": run.verification_level,
            "iterations": run.iterations, "tool_calls": run.total_tool_calls,
            "tokens_in": run.total_tokens_in, "tokens_out": run.total_tokens_out,
            "wall_ms": run.wall_ms, "heldout_passed": ev.heldout_passed,
            "heldout_total": ev.heldout_total, "success_l5": ev.success,
            "first_patch_success": first, "eval_error": ev.error,
        }  # fmt: skip
        with (exp_dir / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return ev


def _artifact(art: Path | None, art_id: str | None) -> str | None:
    if art is None or not art_id:
        return None
    path = next(iter(sorted(art.glob(f"{art_id}.*"))), None)
    return path.read_text(encoding="utf-8") if path else None


async def run_experiment(
    settings: Settings,
    config: ExperimentConfig,
    *,
    experiment_id: str | None = None,
    execute: bool = True,
    out_dir: Path = Path(".data/experiments"),
    provider_factory: ProviderFactory | None = None,
    allow_scripted: bool = False,
    root: Path | None = None,
) -> str:
    runner = ExperimentRunner(
        settings,
        provider_factory=provider_factory,
        allow_scripted=allow_scripted,
        root=root,
        out_dir=out_dir,
    )
    try:
        return await runner.run(config, experiment_id=experiment_id, execute=execute)
    finally:
        await runner.aclose()


def summary_json(tables: dict[str, Any]) -> str:
    return json.dumps(tables, indent=2, default=str)
