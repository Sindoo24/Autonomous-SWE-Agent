"""PostgreSQL schema. Migrations live in `swe_agent/db/migrations` (Alembic).

Design notes
- `events` is the persisted trajectory (same shape as trajectory.jsonl). Tool calls and model
  calls are SQL views over it (migration 0002), so there is one source of truth for every metric.
- `patches`, `test_runs`, `test_results`, `failures`, `approvals` are projected from the run's
  final (or paused) state by the worker, for SQL analytics. Live views during a run read the
  LangGraph checkpoint instead.
- `jobs` is the work queue: workers claim rows with `FOR UPDATE SKIP LOCKED`, so no broker is
  needed and a job survives any process dying (stale heartbeats are requeued).
- LangGraph's own checkpoint tables are created by its Postgres saver (`checkpoints*`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[str]: ARRAY(Text)}


TS = DateTime(timezone=True)


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"))
    issue_text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[dict[str, Any] | None]
    benchmark_task_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_tasks.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16), default="running")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "idempotency_key", name="uq_run_idempotency"),
        Index("ix_runs_experiment", "experiment_id"),
        Index("ix_runs_status", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    # agent | baseline_single_shot | baseline_react
    system: Mapped[str] = mapped_column(String(32), default="agent")
    # ablation variant of the system ("full", "no_reproduce", ...)
    variant: Mapped[str] = mapped_column(String(32), default="full")
    # effective model settings (api key removed) and the overrides this run was started with
    model_config_: Mapped[dict[str, Any] | None] = mapped_column("model_config", JSONB)
    overrides: Mapped[dict[str, Any] | None]
    seed: Mapped[int | None] = mapped_column(Integer)
    # queued | running | awaiting_approval | finished | failed | cancelled
    status: Mapped[str] = mapped_column(String(24), default="queued")
    termination_reason: Mapped[str | None] = mapped_column(String(32))
    verification_level: Mapped[int | None] = mapped_column(SmallInteger)
    verification_status: Mapped[str | None] = mapped_column(String(16))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    artifacts_dir: Mapped[str | None] = mapped_column(Text)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id"))
    repeat: Mapped[int | None] = mapped_column(Integer)
    iterations: Mapped[int | None] = mapped_column(Integer)
    total_tool_calls: Mapped[int | None] = mapped_column(Integer)
    total_model_calls: Mapped[int | None] = mapped_column(Integer)
    total_tokens_in: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens_out: Mapped[int | None] = mapped_column(BigInteger)
    wall_ms: Mapped[int | None] = mapped_column(BigInteger)
    # files the run cited as evidence, planned to change, or changed: reads of any other file
    # count as unnecessary tool calls (evaluation metric; see migration 0003)
    cited_files: Mapped[list[str] | None]
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_event", "run_id", "event"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS)
    event: Mapped[str] = mapped_column(String(32))
    node: Mapped[str | None] = mapped_column(String(32))
    iteration: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]]


class Patch(Base):
    __tablename__ = "patches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    iteration: Mapped[int] = mapped_column(Integer)
    diff_sha256: Mapped[str | None] = mapped_column(String(64))
    diff_artifact_id: Mapped[str | None] = mapped_column(String(64))
    files_changed: Mapped[list[str]]
    lines_added: Mapped[int] = mapped_column(Integer)
    lines_removed: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[bool] = mapped_column(Boolean)
    errors: Mapped[list[str]]
    deviation: Mapped[dict[str, Any] | None]
    tamper: Mapped[bool] = mapped_column(Boolean, default=False)


class TestRunRow(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    command: Mapped[list[str]]
    exit_code: Mapped[int | None] = mapped_column(Integer)
    n_passed: Mapped[int] = mapped_column(Integer)
    n_failed: Mapped[int] = mapped_column(Integer)
    n_errors: Mapped[int] = mapped_column(Integer)
    n_collection_errors: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    log_artifact_id: Mapped[str | None] = mapped_column(String(64))


class TestResultRow(Base):
    __test__ = False
    __tablename__ = "test_results"
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16))  # passed | failed | error


class Failure(Base):
    __tablename__ = "failures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))
    signature: Mapped[str] = mapped_column(String(64))
    route: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    feedback: Mapped[str | None] = mapped_column(Text)
    retry: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # start | resume | continue
    payload: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TS)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class BenchmarkTaskRow(Base):
    __tablename__ = "benchmark_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    difficulty: Mapped[str] = mapped_column(String(8))
    issue_text: Mapped[str] = mapped_column(Text)
    expected_behavior: Mapped[str] = mapped_column(Text)
    heldout_total: Mapped[int] = mapped_column(Integer)
    visible_failing: Mapped[list[str]]


class Evaluation(Base):
    """Held-out evaluation of one run's final patch on a fresh copy of its benchmark task."""

    __tablename__ = "evaluations"
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    benchmark_task_id: Mapped[str] = mapped_column(ForeignKey("benchmark_tasks.id"))
    patch_applied: Mapped[bool] = mapped_column(Boolean)
    heldout_total: Mapped[int] = mapped_column(Integer)
    heldout_passed: Mapped[int] = mapped_column(Integer)
    heldout_failed: Mapped[list[str]]
    visible_regressions: Mapped[list[str]]
    success: Mapped[bool] = mapped_column(Boolean)
    # iteration-1 patch evaluated too (first-attempt success); NULL when not evaluated
    first_patch_success: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
