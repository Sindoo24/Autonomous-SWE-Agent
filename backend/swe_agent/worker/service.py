"""The worker: takes jobs from the Postgres queue and executes runs.

Lifecycle of a run
    POST /tasks/{id}/runs      -> agent_runs(status=queued) + jobs(kind=start)
    worker claims the job      -> status=running; events stream into `events`
    graph pauses for approval  -> status=awaiting_approval (job done)
    POST /runs/{id}/approve    -> jobs(kind=resume, payload=decision)
    worker resumes             -> status=finished (report.json, final.patch)

Robustness
- Jobs are claimed with FOR UPDATE SKIP LOCKED: any number of workers, no double execution.
- A heartbeat is written while a job runs. A job whose worker died is requeued as `continue`
  (by any worker), and the run continues from its last checkpoint instead of starting over.
- Cancellation is cooperative: the API sets agent_runs.cancel_requested; the worker polls it
  and the graph checks it before every node (terminal nodes still run and write the report).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from swe_agent.agents.runner import RunNotFoundError, RunOutcome, continue_run, resume_run
from swe_agent.config import ModelSettings, Settings, apply_overrides
from swe_agent.db import store
from swe_agent.db.engine import (
    DatabaseNotConfiguredError,
    make_async_engine,
    make_sync_engine,
    session_factory,
)
from swe_agent.db.models import AgentRun, Job
from swe_agent.db.sink import DbEventSink
from swe_agent.evaluation.systems import systems
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.factory import build_provider
from swe_agent.observability.logging import get_logger
from swe_agent.observability.trajectory import EventSink
from swe_agent.schemas.state import ApprovalDecision

_log = get_logger("worker")
ProviderFactory = Callable[[ModelSettings], LLMProvider]


def model_config_of(settings: Settings) -> dict[str, Any]:
    """Model settings recorded with each run (secrets removed)."""
    return settings.model.model_dump(mode="json", exclude={"api_key"})


class Worker:
    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        if not settings.database_url:
            raise DatabaseNotConfiguredError()
        self.settings = settings
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.provider_factory = provider_factory or build_provider
        self.engine = make_async_engine(settings.database_url)
        self.sync_engine = make_sync_engine(settings.database_url)
        self.sessions = session_factory(self.engine)

    async def aclose(self) -> None:
        await self.engine.dispose()
        self.sync_engine.dispose()

    # ------------------------------------------------------------------ loop

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        _log.info("worker_started", worker=self.id)
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception as exc:
                _log.error("worker_error", error=str(exc)[:1000])
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.settings.worker_poll_seconds)
        _log.info("worker_stopped", worker=self.id)

    async def run_once(self) -> bool:
        """Requeue stale jobs, then claim and execute at most one job. False if idle."""
        async with self.sessions() as s, s.begin():
            requeued = await store.requeue_stale(
                s, self.settings.worker_stale_seconds, self.settings.job_max_attempts
            )
        if requeued:
            _log.warning("jobs_requeued", jobs=requeued)
        async with self.sessions() as s, s.begin():
            job = await store.claim_job(s, self.id)
        if job is None:
            return False
        await self.process(job)
        return True

    # ------------------------------------------------------------------ one job

    async def process(self, job: Job) -> None:
        async with self.sessions() as s:
            run = await store.get_run(s, job.run_id)
            found = await store.task_with_source(s, run.task_id) if run else None
        if run is None or found is None:
            async with self.sessions() as s, s.begin():
                await store.finish_job(s, job.id, "failed", "run or task not found")
            return
        task, source = found
        _log.info("job_started", job=job.id, kind=job.kind, run=run.id, system=run.system)
        settings = apply_overrides(self.settings, run.overrides)
        cancel = threading.Event()
        sink = DbEventSink(self.sync_engine)
        sinks: list[EventSink] = [sink]  # spans are added by the runner itself
        async with self.sessions() as s, s.begin():
            await store.set_run(
                s,
                run.id,
                status="running",
                started_at=run.started_at or datetime.now(UTC),
                model_config_=model_config_of(settings),
                seed=settings.model.seed,
                artifacts_dir=str(settings.artifacts_root.resolve() / task.id / run.id),
            )
        watcher = asyncio.create_task(self._watch(job.id, run.id, cancel))
        provider = self.provider_factory(settings.model)
        outcome: RunOutcome | None = None
        error: str | None = None
        try:
            outcome = await self._dispatch(job, run, task.issue_text, source, settings,
                                           provider, sinks, cancel)  # fmt: skip
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _log.error("job_failed", job=job.id, run=run.id, error=error[:1000])
        finally:
            watcher.cancel()
            await provider.aclose()
            await asyncio.to_thread(sink.close)
        await self._record(job, run, outcome, error)

    async def _dispatch(
        self,
        job: Job,
        run: AgentRun,
        issue: str,
        source: str,
        settings: Settings,
        provider: LLMProvider,
        sinks: list[EventSink],
        cancel: threading.Event,
    ) -> RunOutcome:
        kw: dict[str, Any] = {"provider": provider, "sinks": sinks, "cancel": cancel.is_set}
        if job.kind == "resume":
            decision = ApprovalDecision.model_validate((job.payload or {})["decision"])
            try:
                return await resume_run(run.id, decision, settings, **kw)
            except RunNotFoundError:
                pass  # already past the approval (a previous attempt resumed it): continue
        if job.kind in ("resume", "continue"):
            out = await continue_run(run.id, settings, **kw)
            if out is not None:
                return out
        fn = systems()[run.system]
        return await fn(source, issue, settings, task_id=run.task_id, run_id=run.id, **kw)

    async def _watch(self, job_id: int, run_id: str, cancel: threading.Event) -> None:
        """Heartbeat the job and pick up cancel requests while the run executes."""
        while True:
            try:
                async with self.sessions() as s, s.begin():
                    await store.heartbeat(s, job_id)
                    run = await store.get_run(s, run_id)
                    if run is not None and run.cancel_requested:
                        cancel.set()
            except Exception as exc:
                _log.warning("heartbeat_failed", job=job_id, error=str(exc)[:300])
            await asyncio.sleep(self.settings.worker_heartbeat_seconds)

    async def _record(
        self, job: Job, run: AgentRun, outcome: RunOutcome | None, error: str | None
    ) -> None:
        async with self.sessions() as s, s.begin():
            if outcome is None:
                await store.set_run(s, run.id, status="failed", error=(error or "")[:4000],
                                    finished_at=datetime.now(UTC))  # fmt: skip
                await store.finish_job(s, job.id, "failed", (error or "")[:4000])
                return
            await store.project_state(s, run.id, outcome.state)
            cited = set(store.cited_files(outcome.state))
            cited |= set(outcome.report.get("files_changed") or [])
            await store.set_run(s, run.id, cited_files=sorted(cited))
            if outcome.pending_approval is not None:
                ver = outcome.pending_approval.get("verification") or {}
                await store.set_run(s, run.id, status="awaiting_approval",
                                    verification_level=ver.get("level"),
                                    verification_status=ver.get("status"))  # fmt: skip
            else:
                summary = store.summary_from_report(outcome.report)
                status = "cancelled" if summary["termination_reason"] == "cancelled" else "finished"
                await store.set_run(s, run.id, status=status, finished_at=datetime.now(UTC),
                                    **summary)  # fmt: skip
            await store.finish_job(s, job.id, "done")
        _log.info("job_finished", job=job.id, run=run.id)
