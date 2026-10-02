"""Run the agent on benchmark tasks and score each run with held-out tests.

Results are appended to `results.jsonl` (one row per task x repeat) and summarised from that
file only. A scripted provider is refused: its "results" would not be model performance.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from swe_agent.agents.runner import run_task
from swe_agent.config import Settings
from swe_agent.evaluation.benchmark.evaluate import evaluate_patch
from swe_agent.evaluation.benchmark.tasks import load_tasks, materialize
from swe_agent.llm.base import LLMProvider


async def run_benchmark(
    settings: Settings,
    out_dir: Path,
    *,
    task_ids: list[str] | None = None,
    repeats: int = 1,
    provider: LLMProvider | None = None,
    root: Path | None = None,
    allow_scripted: bool = False,
) -> list[dict[str, Any]]:
    root = root or settings.benchmarks_dir.resolve()
    if provider is not None and provider.name == "scripted" and not allow_scripted:
        raise ValueError("refusing to benchmark a scripted provider")
    if not settings.sandbox.enabled:
        raise ValueError("benchmarking requires the sandbox (SANDBOX_ENABLED=true)")
    # Benchmark patches are scored by held-out tests on a fresh copy, never applied anywhere:
    # there is nothing for a human to approve.
    settings = settings.model_copy(deep=True)
    settings.agent.approval = "auto"
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    rows: list[dict[str, Any]] = []
    m = settings.model
    for task in load_tasks(root, task_ids):
        for rep in range(repeats):
            repo = materialize(
                task, out_dir / "repos" / f"{task.id}-{rep}-{int(time.time())}", root
            )
            t0 = time.monotonic()
            outcome = await run_task(str(repo), task.issue, settings, provider=provider)
            wall = time.monotonic() - t0
            patch_file = outcome.artifacts_dir / "final.patch"
            patch = patch_file.read_text() if patch_file.exists() else None
            ev = await asyncio.to_thread(evaluate_patch, task, patch, settings.sandbox, root)
            r = outcome.report
            trace = r.get("trace", {})
            row = {
                "task": task.id,
                "category": task.category,
                "difficulty": task.difficulty,
                "repeat": rep,
                "system": "agent",
                "provider": m.provider.value if provider is None else provider.name,
                "models": {"reasoning": m.reasoning, "coder": m.coder},
                "tool_mode": m.tool_mode.value,
                "seed": m.seed,
                "task_run": outcome.task_id,
                "run_id": outcome.run_id,
                "termination_reason": r.get("termination_reason"),
                "agent_status": r.get("status"),
                "agent_level": r.get("verification", {}).get("level"),
                "reproduced": bool(r.get("reproduction")),
                "rollbacks": r.get("rollbacks"),
                "iterations": r.get("iterations"),
                "tool_calls": trace.get("tool_calls"),
                "model_calls": trace.get("model_calls"),
                "tokens_in": trace.get("tokens_in"),
                "tokens_out": trace.get("tokens_out"),
                "model_latency_ms": trace.get("model_latency_ms"),
                "sandbox_ms": trace.get("sandbox_ms"),
                "wall_s": round(wall, 1),
                "heldout_passed": ev.heldout_passed,
                "heldout_total": ev.heldout_total,
                "visible_regressions": ev.visible_regressions,
                "success_l5": ev.success,
                "eval_error": ev.error,
                "artifacts": str(outcome.artifacts_dir),
            }
            rows.append(row)
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"runs": 0}
    n = len(rows)
    ok = sum(1 for r in rows if r["success_l5"])

    def mean(key: str) -> float | None:
        vals = [r[key] for r in rows if isinstance(r.get(key), int | float)]
        return round(statistics.mean(vals), 2) if vals else None

    return {
        "runs": n,
        "success_l5": ok,
        "success_rate": round(ok / n, 3),
        "by_task": {
            t: [r["success_l5"] for r in rows if r["task"] == t]
            for t in sorted({r["task"] for r in rows})
        },
        "termination": {
            k: sum(1 for r in rows if r["termination_reason"] == k)
            for k in sorted({str(r["termination_reason"]) for r in rows})
        },
        "mean_iterations": mean("iterations"),
        "mean_tool_calls": mean("tool_calls"),
        "mean_tokens_in": mean("tokens_in"),
        "mean_tokens_out": mean("tokens_out"),
        "mean_wall_s": mean("wall_s"),
    }
