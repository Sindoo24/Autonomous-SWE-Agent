"""Experiment metrics, read from the SQL views of migration 0003. Nothing is computed here
beyond selecting and grouping rows, so every reported number can be re-derived in SQL."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _plain(v: Any) -> Any:
    """Postgres sum() of integers is NUMERIC (Decimal): return int / float instead."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


async def _rows(s: AsyncSession, sql: str, exp_id: str) -> list[dict[str, Any]]:
    res = await s.execute(text(sql), {"e": exp_id})
    return [{k: _plain(v) for k, v in r._mapping.items()} for r in res]


async def experiment_tables(s: AsyncSession, exp_id: str) -> dict[str, Any] | None:
    exp = await _rows(s, "SELECT * FROM experiments WHERE id = :e", exp_id)
    if not exp:
        return None
    order = "ORDER BY system, variant"
    return {
        "experiment": exp[0],
        "summary": await _rows(
            s, f"SELECT * FROM v_experiment_summary WHERE experiment_id = :e {order}", exp_id
        ),
        "repeat_success": await _rows(
            s,
            f"SELECT * FROM v_experiment_repeat_success WHERE experiment_id = :e {order}, repeat",
            exp_id,
        ),
        "by_difficulty": await _rows(
            s,
            "SELECT system, variant, difficulty, sum(runs) AS runs, sum(successes) AS successes "
            "FROM v_experiment_breakdown WHERE experiment_id = :e "
            f"GROUP BY system, variant, difficulty {order}, difficulty",
            exp_id,
        ),
        "by_category": await _rows(
            s,
            "SELECT system, variant, category, sum(runs) AS runs, sum(successes) AS successes "
            "FROM v_experiment_breakdown WHERE experiment_id = :e "
            f"GROUP BY system, variant, category {order}, category",
            exp_id,
        ),
        "terminations": await _rows(
            s,
            f"SELECT * FROM v_experiment_terminations WHERE experiment_id = :e {order}, reason",
            exp_id,
        ),
        "failure_categories": await _rows(
            s,
            "SELECT * FROM v_experiment_failure_categories WHERE experiment_id = :e "
            f"{order}, category",
            exp_id,
        ),
        "tool_usage": await _rows(
            s,
            f"SELECT * FROM v_experiment_tool_usage WHERE experiment_id = :e {order}, calls DESC",
            exp_id,
        ),
        "per_task": await _rows(
            s,
            "SELECT benchmark_task, difficulty, category, system, variant, count(*) AS runs, "
            "count(*) FILTER (WHERE success) AS successes, "
            "count(*) FILTER (WHERE success IS NULL) AS unevaluated "
            "FROM v_eval_runs WHERE experiment_id = :e "
            "GROUP BY benchmark_task, difficulty, category, system, variant "
            "ORDER BY benchmark_task, system, variant",
            exp_id,
        ),
        "runs": await _rows(
            s,
            "SELECT run_id, benchmark_task, system, variant, repeat, seed, status, "
            "termination_reason, verification_level, success, first_patch_success, "
            "heldout_passed, heldout_total, iterations, total_tool_calls, total_tokens_in, "
            "total_tokens_out, wall_ms, eval_error FROM v_eval_runs WHERE experiment_id = :e "
            "ORDER BY repeat, benchmark_task, system, variant",
            exp_id,
        ),
    }
