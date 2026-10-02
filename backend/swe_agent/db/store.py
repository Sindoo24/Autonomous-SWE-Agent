"""Data access for the API, the worker and the experiment runner (async SQLAlchemy)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from swe_agent.core.ids import new_id
from swe_agent.db.models import (
    AgentRun,
    Approval,
    Event,
    Failure,
    Job,
    Patch,
    Repository,
    Task,
    TestResultRow,
    TestRunRow,
)
from swe_agent.schemas.state import FailureAnalysis, PatchIteration, TestRun
from swe_agent.verification.patch_validator import TAMPER_PATTERNS

ACTIVE = ("queued", "running")


def now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- tasks and runs


async def ensure_repository(s: AsyncSession, source: str) -> Repository:
    repo = (await s.execute(select(Repository).where(Repository.source == source))).scalar()
    if repo is None:
        repo = Repository(id=new_id("repo"), source=source)
        s.add(repo)
        await s.flush()
    return repo


async def create_task(
    s: AsyncSession,
    source: str,
    issue: str,
    *,
    created_by: str | None = None,
    budget: dict[str, Any] | None = None,
    benchmark_task_id: str | None = None,
) -> Task:
    repo = await ensure_repository(s, source)
    task = Task(
        id=new_id("t"),
        repository_id=repo.id,
        issue_text=issue,
        created_by=created_by,
        budget=budget,
        benchmark_task_id=benchmark_task_id,
    )
    s.add(task)
    await s.flush()
    return task


async def task_with_source(s: AsyncSession, task_id: str) -> tuple[Task, str] | None:
    row = (
        await s.execute(select(Task, Repository.source).join(Repository).where(Task.id == task_id))
    ).first()
    return (row[0], row[1]) if row else None


async def create_run(
    s: AsyncSession,
    task_id: str,
    *,
    system: str = "agent",
    variant: str = "full",
    overrides: dict[str, Any] | None = None,
    model_config: dict[str, Any] | None = None,
    seed: int | None = None,
    idempotency_key: str | None = None,
    experiment_id: str | None = None,
    repeat: int | None = None,
) -> tuple[AgentRun, bool]:
    """Create a run and enqueue its start job in the same transaction. With an idempotency key
    the existing run for (task, key) is returned instead (created = False)."""
    if idempotency_key:
        existing = (
            await s.execute(
                select(AgentRun).where(
                    AgentRun.task_id == task_id, AgentRun.idempotency_key == idempotency_key
                )
            )
        ).scalar()
        if existing is not None:
            return existing, False
    run = AgentRun(
        id=new_id("r"),
        task_id=task_id,
        system=system,
        variant=variant,
        overrides=overrides or {},
        model_config_=model_config,
        seed=seed,
        status="queued",
        idempotency_key=idempotency_key,
        experiment_id=experiment_id,
        repeat=repeat,
        cancel_requested=False,
    )
    s.add(run)
    await s.flush()
    await enqueue(s, run.id, "start")
    return run, True


async def get_run(s: AsyncSession, run_id: str) -> AgentRun | None:
    return await s.get(AgentRun, run_id)


async def list_runs(
    s: AsyncSession,
    *,
    task_id: str | None = None,
    experiment_id: str | None = None,
    limit: int = 50,
) -> list[AgentRun]:
    q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    if task_id:
        q = q.where(AgentRun.task_id == task_id)
    if experiment_id:
        q = q.where(AgentRun.experiment_id == experiment_id)
    return list((await s.execute(q)).scalars())


async def set_run(s: AsyncSession, run_id: str, **values: Any) -> None:
    await s.execute(update(AgentRun).where(AgentRun.id == run_id).values(**values))


async def events_after(
    s: AsyncSession, run_id: str, after_seq: int = 0, limit: int = 500
) -> list[Event]:
    q = (
        select(Event)
        .where(Event.run_id == run_id, Event.seq > after_seq)
        .order_by(Event.seq)
        .limit(limit)
    )
    return list((await s.execute(q)).scalars())


async def list_tasks(s: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    rows = await s.execute(
        text(
            "SELECT t.id, t.issue_text, t.created_at, r.source, t.benchmark_task_id "
            "FROM tasks t JOIN repositories r ON r.id = t.repository_id "
            "ORDER BY t.created_at DESC LIMIT :n"
        ),
        {"n": limit},
    )
    return [dict(r._mapping) for r in rows]


async def last_event_seq(s: AsyncSession, run_id: str) -> int:
    last = await s.execute(text("SELECT max(seq) FROM events WHERE run_id = :r"), {"r": run_id})
    return int(last.scalar() or 0)


async def ping(s: AsyncSession) -> None:
    await s.execute(text("SELECT 1"))


# --------------------------------------------------------------------------- read-side views


async def run_metrics(s: AsyncSession, run_id: str) -> dict[str, Any]:
    """Per-run latency, tokens, tool usage and node durations from the observability views."""
    lat = (await s.execute(text("SELECT * FROM v_run_latency WHERE run_id = :r"),
                           {"r": run_id})).first()  # fmt: skip
    tok = (await s.execute(text("SELECT * FROM v_run_tokens WHERE run_id = :r"),
                           {"r": run_id})).first()  # fmt: skip
    tools = await s.execute(
        text("SELECT * FROM v_run_tool_usage WHERE run_id = :r ORDER BY calls DESC"),
        {"r": run_id},
    )
    nodes = await s.execute(
        text("SELECT node, count(*) AS runs, sum(duration_ms) AS total_ms "
             "FROM v_node_durations WHERE run_id = :r GROUP BY node ORDER BY total_ms DESC"),
        {"r": run_id},
    )  # fmt: skip
    return {
        "latency": dict(lat._mapping) if lat else None,
        "tokens": dict(tok._mapping) if tok else None,
        "tools": [dict(r._mapping) for r in tools],
        "nodes": [dict(r._mapping) for r in nodes],
    }


async def list_experiments(s: AsyncSession) -> list[dict[str, Any]]:
    rows = await s.execute(text(
        "SELECT e.id, e.name, e.status, e.created_at, e.finished_at, e.config, "
        "count(r.id) AS runs FROM experiments e LEFT JOIN agent_runs r "
        "ON r.experiment_id = e.id GROUP BY e.id ORDER BY e.created_at DESC"))  # fmt: skip
    return [dict(r._mapping) for r in rows]


# --------------------------------------------------------------------------- job queue


async def enqueue(
    s: AsyncSession, run_id: str, kind: str, payload: dict[str, Any] | None = None
) -> Job:
    job = Job(run_id=run_id, kind=kind, payload=payload, status="queued", attempts=0)
    s.add(job)
    await s.flush()
    return job


async def active_job(s: AsyncSession, run_id: str) -> Job | None:
    q = select(Job).where(Job.run_id == run_id, Job.status.in_(ACTIVE)).limit(1)
    return (await s.execute(q)).scalar()


_CLAIM = text(
    """
    UPDATE jobs SET status = 'running', locked_by = :worker, heartbeat_at = now(),
                    attempts = attempts + 1
    WHERE id = (
        SELECT id FROM jobs WHERE status = 'queued'
        ORDER BY created_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


async def claim_job(s: AsyncSession, worker_id: str) -> Job | None:
    """Atomically take the oldest queued job. Concurrent workers never get the same job."""
    job_id = (await s.execute(_CLAIM, {"worker": worker_id})).scalar()
    if job_id is None:
        return None
    return await s.get(Job, job_id, populate_existing=True)


async def claim_specific(s: AsyncSession, job_id: int, worker_id: str) -> Job | None:
    """Claim one particular queued job (the experiment runner executing its own runs inline)."""
    res = await s.execute(
        text(
            "UPDATE jobs SET status = 'running', locked_by = :w, heartbeat_at = now(), "
            "attempts = attempts + 1 WHERE id = :id AND status = 'queued' RETURNING id"
        ),
        {"w": worker_id, "id": job_id},
    )
    if res.scalar() is None:
        return None
    return await s.get(Job, job_id, populate_existing=True)


async def heartbeat(s: AsyncSession, job_id: int) -> None:
    await s.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=now()))


async def finish_job(s: AsyncSession, job_id: int, status: str, error: str | None = None) -> None:
    await s.execute(
        update(Job).where(Job.id == job_id).values(status=status, error=error, finished_at=now())
    )


async def requeue_stale(s: AsyncSession, stale_seconds: float, max_attempts: int) -> list[int]:
    """Jobs whose worker stopped sending heartbeats. They are retried as `continue` (the run
    resumes from its last checkpoint) until max_attempts, then marked failed."""
    cutoff = now() - timedelta(seconds=stale_seconds)
    q = (
        select(Job)
        .where(Job.status == "running", Job.heartbeat_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    requeued = []
    for job in (await s.execute(q)).scalars():
        if job.attempts >= max_attempts:
            job.status = "failed"
            job.error = f"worker lost {job.attempts} times"
            job.finished_at = now()
            await set_run(s, job.run_id, status="failed", error=job.error, finished_at=now())
        else:
            job.status = "queued"
            job.kind = "continue" if job.kind == "start" else job.kind
            job.locked_by = None
            requeued.append(job.id)
    return requeued


# --------------------------------------------------------------------------- projections


async def record_approval(
    s: AsyncSession, run_id: str, approved: bool, feedback: str | None, retry: bool, by: str
) -> None:
    s.add(Approval(run_id=run_id, approved=approved, feedback=feedback, retry=retry,
                   decided_by=by))  # fmt: skip


async def project_state(s: AsyncSession, run_id: str, state: dict[str, Any]) -> None:
    """Write the analytics tables (patches, test runs, per-test results, failures) from a run's
    state. Idempotent: the run's previous projection is replaced."""
    for model in (Patch, Failure):
        await s.execute(delete(model).where(model.run_id == run_id))
    old = select(TestRunRow.id).where(TestRunRow.run_id == run_id)
    await s.execute(delete(TestResultRow).where(TestResultRow.test_run_id.in_(old)))
    await s.execute(delete(TestRunRow).where(TestRunRow.run_id == run_id))

    patches: list[PatchIteration] = state.get("patch_history", [])
    for i, p in enumerate(patches):
        s.add(
            Patch(
                run_id=run_id,
                seq=i,
                iteration=p.iteration,
                diff_sha256=p.diff_sha256,
                diff_artifact_id=p.diff_artifact_id,
                files_changed=list(p.files_changed),
                lines_added=p.lines_added,
                lines_removed=p.lines_removed,
                accepted=p.validation.accepted,
                errors=list(p.validation.errors),
                deviation=p.deviation.model_dump(mode="json"),
                tamper=any(label in e for e in p.validation.errors for _, label in TAMPER_PATTERNS),
            )
        )
    runs: list[TestRun] = state.get("test_runs", [])
    seen: set[str] = set()
    for i, r in enumerate(runs):
        if r.id in seen:
            continue
        seen.add(r.id)
        s.add(
            TestRunRow(
                id=r.id,
                run_id=run_id,
                seq=i,
                kind=r.kind.value,
                status=r.status.value,
                command=list(r.command),
                exit_code=r.exit_code,
                n_passed=len(r.passed),
                n_failed=len(r.failed),
                n_errors=len(r.errors),
                n_collection_errors=len(r.collection_errors),
                duration_ms=r.duration_ms,
                timed_out=r.timed_out,
                log_artifact_id=r.log_artifact_id,
            )
        )
        await s.flush()
        outcomes = {n: "passed" for n in r.passed} | {n: "failed" for n in r.failed}
        outcomes |= {n: "error" for n in r.errors}
        for node, outcome in outcomes.items():
            s.add(TestResultRow(test_run_id=r.id, node_id=node, outcome=outcome))
    failures: list[FailureAnalysis] = state.get("failure_history", [])
    for i, f in enumerate(failures):
        s.add(Failure(run_id=run_id, seq=i, category=f.category.value, signature=f.signature,
                      route=f.route, summary=f.summary[:2000]))  # fmt: skip
    await s.flush()


def summary_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """agent_runs summary columns from report.json (None when the report lacks them)."""
    trace = report.get("trace") or {}
    ver = report.get("verification") or {}
    latency = report.get("latency_s")
    return {
        "termination_reason": report.get("termination_reason"),
        "verification_level": ver.get("level"),
        "verification_status": ver.get("status"),
        "iterations": report.get("iterations"),
        "total_tool_calls": trace.get("tool_calls"),
        "total_model_calls": trace.get("model_calls"),
        "total_tokens_in": trace.get("tokens_in"),
        "total_tokens_out": trace.get("tokens_out"),
        "wall_ms": int(latency * 1000) if isinstance(latency, int | float) else None,
    }


def cited_files(state: dict[str, Any]) -> list[str]:
    """Files a run actually used: cited as evidence, planned, changed, or its reproduction test.
    Reads of other files are counted as unnecessary tool calls by the evaluation views."""
    out: set[str] = set()
    if (f := state.get("findings")) is not None:
        out |= set(f.relevant_files) | {e.path for e in f.evidence}
    if (h := state.get("hypotheses")) is not None:
        out |= {e.path for hyp in h.hypotheses for e in hyp.evidence}
    for plan in [*state.get("plan_history", []), *([p] if (p := state.get("plan")) else [])]:
        out |= set(plan.files_to_modify) | {c.file for c in plan.changes}
    for patch in state.get("patch_history", []):
        out |= set(patch.files_changed)
    if (r := state.get("repro")) is not None:
        out.add(r.path)
    return sorted(out)
