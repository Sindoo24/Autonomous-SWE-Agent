"""Observability views: tool / model / sandbox calls, node durations, latency and token accounting.

Every metric is a view over `events` (the persisted trajectory), so numbers can always be traced
back to the individual events that produced them.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

VIEWS = {
    "v_tool_calls": """
        SELECT run_id, seq, ts, node, iteration,
               data->>'tool'                              AS tool,
               data->>'status'                            AS status,
               data->>'error_type'                        AS error_type,
               data->>'error'                             AS error,
               COALESCE((data->>'duration_ms')::int, 0)  AS duration_ms,
               COALESCE((data->>'system')::boolean, false)    AS system,
               COALESCE((data->>'repeated')::boolean, false)  AS repeated,
               COALESCE((data->>'truncated')::boolean, false) AS truncated,
               data->'args'                               AS args,
               data->'args'->>'path'                      AS path
        FROM events WHERE event = 'tool_call'
    """,
    "v_model_calls": """
        SELECT run_id, seq, ts, node, iteration,
               data->>'role'                 AS role,
               data->>'purpose'              AS purpose,
               data->>'provider'             AS provider,
               data->>'model'                AS model,
               data->>'status'               AS status,
               (data->>'tokens_in')::int     AS tokens_in,
               (data->>'tokens_out')::int    AS tokens_out,
               (data->>'latency_ms')::int    AS latency_ms,
               COALESCE(data->>'purpose' LIKE '%repair', false) AS repaired
        FROM events WHERE event = 'model_call'
    """,
    "v_sandbox_execs": """
        SELECT run_id, seq, ts, node, iteration,
               data->>'op'                  AS op,
               data->>'status'              AS status,
               (data->>'duration_ms')::int  AS duration_ms,
               COALESCE((data->>'timed_out')::boolean, false)  AS timed_out,
               COALESCE((data->>'oom_killed')::boolean, false) AS oom_killed
        FROM events WHERE event = 'sandbox_exec'
    """,
    "v_node_durations": """
        SELECT run_id, seq, ts, node, iteration,
               data->>'status'              AS status,
               (data->>'duration_ms')::int  AS duration_ms
        FROM events WHERE event = 'node_finished'
    """,
    # active_ms = time spent inside nodes (excludes time paused for approval); the rest of the
    # breakdown attributes it to model calls, tool calls and sandbox executions.
    "v_run_latency": """
        SELECT r.id AS run_id,
               COALESCE(n.active_ms, 0)  AS active_ms,
               COALESCE(m.model_ms, 0)   AS model_ms,
               COALESCE(t.tool_ms, 0)    AS tool_ms,
               COALESCE(x.sandbox_ms, 0) AS sandbox_ms,
               GREATEST(COALESCE(n.active_ms, 0) - COALESCE(m.model_ms, 0)
                        - COALESCE(t.tool_ms, 0) - COALESCE(x.sandbox_ms, 0), 0) AS other_ms,
               r.wall_ms
        FROM agent_runs r
        LEFT JOIN (SELECT run_id, sum(duration_ms) AS active_ms FROM v_node_durations
                   GROUP BY run_id) n ON n.run_id = r.id
        LEFT JOIN (SELECT run_id, sum(latency_ms) AS model_ms FROM v_model_calls
                   GROUP BY run_id) m ON m.run_id = r.id
        LEFT JOIN (SELECT run_id, sum(duration_ms) AS tool_ms FROM v_tool_calls
                   WHERE NOT system GROUP BY run_id) t ON t.run_id = r.id
        LEFT JOIN (SELECT run_id, sum(duration_ms) AS sandbox_ms FROM v_sandbox_execs
                   GROUP BY run_id) x ON x.run_id = r.id
    """,
    # Token totals are NULL when any call lacked usage data: never estimated.
    "v_run_tokens": """
        SELECT run_id,
               count(*) AS model_calls,
               count(*) FILTER (WHERE tokens_in IS NULL OR tokens_out IS NULL) AS calls_without_usage,
               CASE WHEN count(*) FILTER (WHERE tokens_in IS NULL) > 0 THEN NULL
                    ELSE sum(tokens_in) END  AS tokens_in,
               CASE WHEN count(*) FILTER (WHERE tokens_out IS NULL) > 0 THEN NULL
                    ELSE sum(tokens_out) END AS tokens_out,
               sum(latency_ms) AS model_ms
        FROM v_model_calls GROUP BY run_id
    """,
    "v_run_tool_usage": """
        SELECT run_id, tool,
               count(*)                              AS calls,
               count(*) FILTER (WHERE status <> 'ok') AS errors,
               count(*) FILTER (WHERE repeated)       AS repeated,
               sum(duration_ms)                       AS total_ms
        FROM v_tool_calls WHERE NOT system GROUP BY run_id, tool
    """,
}


def upgrade() -> None:
    for name, sql in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {sql}")


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"DROP VIEW IF EXISTS {name}")
