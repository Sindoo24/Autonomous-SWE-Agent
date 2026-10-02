"""Evaluation views. Every number in an experiment report comes from these views.

Definitions (docs/evaluation.md):
- success              held-out tests all pass + no visible regressions, on a fresh copy (L5)
- first-attempt        the run's first accepted patch alone reaches success
- final test-pass rate held-out tests passed / held-out tests, summed over runs (partial credit)
- recovery rate        among runs whose first patch did not succeed, fraction that succeeded
- repeated calls       tool calls identical to an earlier call with no write in between
- uncited reads        successful read_file/outline_file of files never cited as evidence,
                       planned, or changed (the agent cites evidence; baselines only change files,
                       so compare this column within a system, not across systems)
- plan deviation rate  patches whose deviation from the plan is minor/major / patches
- tamper attempts      validator test-tampering rejections + blocked writes to tests/CI/build files

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

VIEWS = {
    "v_eval_runs": """
        SELECT r.id AS run_id, r.experiment_id, r.system, r.variant, r.seed, r.repeat,
               bt.id AS benchmark_task, bt.category, bt.difficulty,
               r.status, r.termination_reason, r.verification_level, r.verification_status,
               r.iterations, r.total_tool_calls, r.total_model_calls,
               r.total_tokens_in, r.total_tokens_out, r.wall_ms,
               e.success, e.first_patch_success, e.heldout_passed, e.heldout_total,
               e.patch_applied, e.visible_regressions, e.error AS eval_error,
               lat.active_ms, lat.model_ms, lat.tool_ms, lat.sandbox_ms,
               (SELECT count(*) FROM v_tool_calls tc
                 WHERE tc.run_id = r.id AND NOT tc.system AND tc.repeated) AS repeated_calls,
               (SELECT count(*) FROM v_tool_calls tc
                 WHERE tc.run_id = r.id AND NOT tc.system AND tc.status = 'ok'
                   AND tc.tool IN ('read_file', 'outline_file') AND tc.path IS NOT NULL
                   AND NOT (tc.path = ANY (COALESCE(r.cited_files, '{}'::text[])))
               ) AS uncited_reads,
               (SELECT count(*) FROM patches p WHERE p.run_id = r.id) AS patches,
               (SELECT count(*) FROM patches p WHERE p.run_id = r.id
                  AND p.deviation->>'severity' IN ('minor', 'major')) AS deviating_patches,
               (SELECT count(*) FROM patches p WHERE p.run_id = r.id AND p.tamper)
               + (SELECT count(*) FROM v_tool_calls tc
                   WHERE tc.run_id = r.id AND tc.error_type = 'policy_violation'
                     AND (tc.error ILIKE '%test files%' OR tc.error ILIKE '%protected%'))
                 AS tamper_attempts
        FROM agent_runs r
        JOIN tasks t ON t.id = r.task_id
        JOIN benchmark_tasks bt ON bt.id = t.benchmark_task_id
        LEFT JOIN evaluations e ON e.run_id = r.id
        LEFT JOIN v_run_latency lat ON lat.run_id = r.id
    """,
    "v_experiment_summary": """
        SELECT experiment_id, system, variant,
               count(*)                                         AS runs,
               count(success)                                   AS evaluated,
               count(*) FILTER (WHERE success)                  AS successes,
               avg(success::int)::float                         AS success_rate,
               avg(first_patch_success::int)::float             AS first_attempt_success_rate,
               (sum(heldout_passed)::float / NULLIF(sum(heldout_total), 0)) AS final_test_pass_rate,
               (count(*) FILTER (WHERE success AND NOT first_patch_success))::float
                 / NULLIF(count(*) FILTER (WHERE NOT first_patch_success), 0) AS recovery_rate,
               avg(iterations)::float                           AS mean_iterations,
               avg(iterations) FILTER (WHERE success)::float    AS mean_iterations_successful,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY wall_ms) AS wall_ms_p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY wall_ms) AS wall_ms_p90,
               avg(model_ms)::float AS mean_model_ms, avg(tool_ms)::float AS mean_tool_ms,
               avg(sandbox_ms)::float AS mean_sandbox_ms,
               avg(total_tokens_in)::float                      AS mean_tokens_in,
               avg(total_tokens_out)::float                     AS mean_tokens_out,
               sum(total_tokens_in)                             AS total_tokens_in,
               sum(total_tokens_out)                            AS total_tokens_out,
               count(*) FILTER (WHERE total_tokens_in IS NULL)  AS runs_without_token_data,
               sum(active_ms)                                   AS total_active_ms,
               avg(total_tool_calls)::float                     AS mean_tool_calls,
               avg(repeated_calls)::float                       AS mean_repeated_calls,
               avg(uncited_reads)::float                        AS mean_uncited_reads,
               (sum(deviating_patches)::float / NULLIF(sum(patches), 0)) AS plan_deviation_rate,
               sum(tamper_attempts)                             AS tamper_attempts
        FROM v_eval_runs WHERE experiment_id IS NOT NULL
        GROUP BY experiment_id, system, variant
    """,
    # success rate per repeat, for the min-max spread across repeats
    "v_experiment_repeat_success": """
        SELECT experiment_id, system, variant, repeat,
               count(*) AS runs, avg(success::int)::float AS success_rate
        FROM v_eval_runs WHERE experiment_id IS NOT NULL
        GROUP BY experiment_id, system, variant, repeat
    """,
    "v_experiment_breakdown": """
        SELECT experiment_id, system, variant, difficulty, category,
               count(*) AS runs, count(*) FILTER (WHERE success) AS successes
        FROM v_eval_runs WHERE experiment_id IS NOT NULL
        GROUP BY experiment_id, system, variant, difficulty, category
    """,
    "v_experiment_terminations": """
        SELECT experiment_id, system, variant, COALESCE(termination_reason, status) AS reason,
               count(*) AS runs
        FROM v_eval_runs WHERE experiment_id IS NOT NULL
        GROUP BY experiment_id, system, variant, COALESCE(termination_reason, status)
    """,
    "v_experiment_failure_categories": """
        SELECT r.experiment_id, r.system, r.variant, f.category, count(*) AS occurrences
        FROM failures f JOIN agent_runs r ON r.id = f.run_id
        WHERE r.experiment_id IS NOT NULL
        GROUP BY r.experiment_id, r.system, r.variant, f.category
    """,
    "v_experiment_tool_usage": """
        SELECT r.experiment_id, r.system, r.variant, u.tool,
               sum(u.calls) AS calls, sum(u.errors) AS errors, sum(u.repeated) AS repeated
        FROM v_run_tool_usage u JOIN agent_runs r ON r.id = u.run_id
        WHERE r.experiment_id IS NOT NULL
        GROUP BY r.experiment_id, r.system, r.variant, u.tool
    """,
}


def upgrade() -> None:
    for name, sql in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {sql}")


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"DROP VIEW IF EXISTS {name}")
