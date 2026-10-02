"""API + worker + PostgreSQL, end to end with a scripted model (no Docker needed: the
sandbox is disabled here; tests/e2e/test_experiment.py covers the executed path)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from swe_agent.config import Settings
from swe_agent.db.engine import make_async_engine, session_factory
from swe_agent.llm.scripted import ScriptedCall, ScriptedProvider, action
from swe_agent.main import create_app
from swe_agent.worker.service import Worker
from tests.integration.test_graph_e2e import (
    API,
    HYPOTHESES,
    IMPL,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = pytest.mark.postgres


def happy() -> list[Any]:
    return [*explore_script(), HYPOTHESES, PLAN, *implement_script()]


class Harness:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self.scripts: list[list[Any]] = []
        self.providers: list[ScriptedProvider] = []

    def factory(self, _model: Any) -> ScriptedProvider:
        p = ScriptedProvider(self.scripts.pop(0) if self.scripts else [])
        self.providers.append(p)
        return p

    async def work(self, *scripts: list[Any]) -> None:
        """Run the worker until the queue is empty, feeding one script per job."""
        self.scripts.extend(scripts)
        worker = Worker(self.settings, worker_id="test-worker", provider_factory=self.factory)
        try:
            while await worker.run_once():
                pass
        finally:
            await worker.aclose()

    async def sql(self, query: str, **params: Any) -> list[Any]:
        engine = make_async_engine(self.settings.database_url or "")
        try:
            async with session_factory(engine)() as s, s.begin():
                res = await s.execute(text(query), params)
                return [tuple(r) for r in res] if res.returns_rows else []
        finally:
            await engine.dispose()

    async def task(self, repo: Path, issue: str = ISSUE) -> str:
        r = await self.client.post("/tasks", json={"repo": str(repo), "issue": issue})
        assert r.status_code == 201, r.text
        return str(r.json()["task_id"])

    async def run(self, task_id: str, **body: Any) -> str:
        r = await self.client.post(f"/tasks/{task_id}/runs", json=body)
        assert r.status_code == 202, r.text
        return str(r.json()["run_id"])


@pytest.fixture
async def h(db_settings: Settings) -> AsyncIterator[Harness]:
    app = create_app(db_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api/api/v1") as client:
            yield Harness(db_settings, client)


# --------------------------------------------------------------------------- tasks / validation


async def test_repository_sources_are_restricted(
    h: Harness, users_repo: Path, tmp_path: Path
) -> None:
    r = await h.client.post("/tasks", json={"repo": "/etc", "issue": ISSUE})
    assert r.status_code == 403, r.text
    for bad in ("file:///etc", "ssh://x/y", "git@github.com:a/b.git", "--upload-pack=x"):
        r = await h.client.post("/tasks", json={"repo": bad, "issue": ISSUE})
        assert r.status_code == 422, bad
    r = await h.client.post("/tasks", json={"repo": "https://github.com/a/b.git", "issue": ISSUE})
    assert r.status_code == 201
    missing = tmp_path / "nope"
    r = await h.client.post("/tasks", json={"repo": str(missing), "issue": ISSUE})
    assert r.status_code == 422
    task = await h.task(users_repo)
    got = (await h.client.get(f"/tasks/{task}")).json()
    assert got["repository"] == str(users_repo) and got["runs"] == []


async def test_api_key(db_settings: Settings) -> None:
    db_settings.api_key = "s3cret"
    app = create_app(db_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api/api/v1"
        ) as c,
    ):
        assert (await c.get("/runs")).status_code == 401
        assert (await c.get("/runs", headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await c.get("/runs", headers={"X-API-Key": "s3cret"})).status_code == 200
        assert (await c.get("/health")).status_code == 200  # liveness stays open


async def test_idempotency_and_override_validation(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    hdr = {"Idempotency-Key": "k1"}
    a = await h.client.post(f"/tasks/{task}/runs", json={}, headers=hdr)
    b = await h.client.post(f"/tasks/{task}/runs", json={}, headers=hdr)
    assert a.json()["run_id"] == b.json()["run_id"] and b.json()["created"] is False
    bad = await h.client.post(
        f"/tasks/{task}/runs", json={"overrides": {"model": {"base_url": "http://evil"}}}
    )
    assert bad.status_code == 422 and "not overridable" in bad.text
    bad = await h.client.post(f"/tasks/{task}/runs", json={"overrides": {"sandbox": {"x": 1}}})
    assert bad.status_code == 422
    assert (await h.client.post("/tasks/t_missing/runs", json={})).status_code == 404
    jobs = await h.sql("SELECT count(*) FROM jobs")
    assert jobs[0][0] == 1  # one start job for the idempotent pair


# --------------------------------------------------------------------------- run lifecycle


async def test_full_lifecycle_with_approval(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    run = await h.run(task)  # approval defaults to manual
    assert (await h.client.get(f"/runs/{run}")).json()["status"] == "queued"
    await h.work(happy())

    st = (await h.client.get(f"/runs/{run}/status")).json()
    assert st["status"] == "awaiting_approval" and st["next"] == ["human_approval"]
    assert st["current_node"] == "human_approval" or st["current_node"] == "validate_patch"
    req = (await h.client.get(f"/runs/{run}/approval")).json()
    assert req["files_changed"] == [API]
    diff = (await h.client.get(f"/runs/{run}/diff")).text
    assert "raise ValidationError" in diff
    plan = (await h.client.get(f"/runs/{run}/plan")).json()
    assert plan["plan"]["files_to_modify"] == [API] and plan["findings"]["relevant_files"]
    patches = (await h.client.get(f"/runs/{run}/patches")).json()
    assert len(patches) == 1 and "raise ValidationError" in patches[0]["diff"]
    assert (await h.client.get(f"/runs/{run}/report")).status_code == 404  # not final yet

    # trajectory paging matches the jsonl file exactly
    page1 = (await h.client.get(f"/runs/{run}/trajectory", params={"limit": 5})).json()
    assert [e["seq"] for e in page1["events"]] == [1, 2, 3, 4, 5]
    rest = (
        await h.client.get(f"/runs/{run}/trajectory", params={"after_seq": page1["next_after_seq"]})
    ).json()
    assert rest["events"][0]["seq"] == 6

    # a second approve while the first is queued is refused
    r = await h.client.post(f"/runs/{run}/approve", json={"decided_by": "sindoori"})
    assert r.status_code == 202
    assert (await h.client.post(f"/runs/{run}/approve", json={})).status_code == 409
    await h.work([])  # approving never calls the model

    run_row = (await h.client.get(f"/runs/{run}")).json()
    assert run_row["status"] == "finished" and run_row["termination_reason"] == "patch_proposed"
    assert run_row["iterations"] == 1 and run_row["tool_calls"] == 5
    report = (await h.client.get(f"/runs/{run}/report")).json()
    assert report["approval"]["decided_by"] == "sindoori"
    final = await h.client.get(f"/runs/{run}/artifacts/final.patch")
    assert final.status_code == 200 and "raise ValidationError" in final.text
    for bad in ("../../etc/passwd", "report.json.bak", "art_1_x/../../y"):
        assert (await h.client.get(f"/runs/{run}/artifacts/{bad}")).status_code == 404

    # every trajectory event is in the database, in order
    art = Path(report["trajectory_file"])
    lines = [json.loads(x) for x in art.read_text().splitlines()]  # noqa: ASYNC240
    rows = await h.sql("SELECT seq, event FROM events WHERE run_id = :r ORDER BY seq", r=run)
    assert [(x["seq"], x["event"]) for x in lines] == [(r[0], r[1]) for r in rows]
    # analytics projections
    assert (await h.sql("SELECT count(*) FROM patches WHERE run_id = :r", r=run))[0][0] == 1
    appr = await h.sql("SELECT approved, decided_by FROM approvals WHERE run_id = :r", r=run)
    assert appr == [(True, "sindoori")]
    cited = (await h.sql("SELECT cited_files FROM agent_runs WHERE id = :r", r=run))[0][0]
    assert API in cited
    # observability views
    m = (await h.client.get(f"/runs/{run}/metrics")).json()
    assert m["tokens"]["model_calls"] == 9
    assert m["latency"]["active_ms"] >= m["latency"]["model_ms"]
    assert {t["tool"] for t in m["tools"]} >= {"read_file", "edit_file"}
    assert any(n["node"] == "explore" for n in m["nodes"])


async def test_reject_with_feedback_replans(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    run = await h.run(task)
    await h.work(happy())
    r = await h.client.post(f"/runs/{run}/reject", json={"retry": True})
    assert r.status_code == 422  # retry needs feedback

    def replan(call: ScriptedCall) -> dict[str, Any]:
        assert "say which field" in call.messages[-1].content
        return PLAN

    better = '        raise ValidationError("email", "email is required (field: email)")\n'
    script = [
        replan,
        action(
            "edit_file",
            path=API,
            search='        raise ValidationError("email", "email is required")\n',
            replace=better,
        ),
        action("finish", **IMPL),
    ]
    r = await h.client.post(
        f"/runs/{run}/reject", json={"feedback": "say which field is missing", "retry": True}
    )
    assert r.status_code == 202
    await h.work(script)
    assert (await h.client.get(f"/runs/{run}")).json()["status"] == "awaiting_approval"
    await h.client.post(f"/runs/{run}/reject", json={"feedback": "no"})
    await h.work([])
    row = (await h.client.get(f"/runs/{run}")).json()
    assert row["status"] == "finished" and row["termination_reason"] == "rejected_by_human"
    decisions = await h.sql(
        "SELECT approved, retry FROM approvals WHERE run_id = :r ORDER BY id", r=run
    )
    assert decisions == [(False, True), (False, False)]


async def test_auto_approval_and_list(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    run = await h.run(task, approval="auto")
    await h.work(happy())
    row = (await h.client.get(f"/runs/{run}")).json()
    assert row["status"] == "finished" and row["verification_status"] == "NOT VERIFIED"
    listed = (await h.client.get("/runs", params={"task_id": task})).json()
    assert [r["id"] for r in listed] == [run]
    assert (await h.client.get("/tasks")).json()[0]["id"] == task


async def test_cancel(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    queued = await h.run(task)
    r = await h.client.post(f"/runs/{queued}/cancel")
    assert r.json()["status"] == "cancelled"
    assert (await h.client.post(f"/runs/{queued}/cancel")).status_code == 409
    await h.work()  # nothing to do: the start job was withdrawn
    assert (await h.client.get(f"/runs/{queued}")).json()["status"] == "cancelled"

    # cancel while awaiting approval: the graph stops at the next node boundary with a report
    waiting = await h.run(task)
    await h.work(happy())
    r = await h.client.post(f"/runs/{waiting}/cancel")
    assert r.json()["status"] == "cancel_requested"
    await h.work([])
    row = (await h.client.get(f"/runs/{waiting}")).json()
    assert row["status"] == "cancelled" and row["termination_reason"] == "cancelled"
    assert (await h.client.get(f"/runs/{waiting}/report")).json()[
        "termination_reason"
    ] == "cancelled"


async def test_worker_failure_is_recorded(h: Harness, users_repo: Path) -> None:
    """A run whose model output is unusable ends with a structured model_error report."""
    task = await h.task(users_repo)
    run = await h.run(task, approval="auto")
    await h.work(["not json", "still not json"])
    row = (await h.client.get(f"/runs/{run}")).json()
    assert row["status"] == "finished" and row["termination_reason"] == "model_error"


class Crash(BaseException):
    """Simulates the worker process dying (not an Exception: nothing catches it)."""


async def test_crashed_worker_run_continues_from_checkpoint(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    run = await h.run(task, approval="auto")

    def die(_call: ScriptedCall) -> str:
        raise Crash

    # explore + hypothesize complete (checkpointed); the process dies during plan
    h.scripts.append([*explore_script(), HYPOTHESES, die])
    worker = Worker(h.settings, worker_id="doomed", provider_factory=h.factory)
    with pytest.raises(Crash):
        await worker.run_once()
    await worker.aclose()
    job = await h.sql("SELECT status, attempts FROM jobs WHERE run_id = :r", r=run)
    assert job == [("running", 1)]

    # nothing happens while the heartbeat is fresh; after it goes stale the job is requeued
    h.settings.worker_stale_seconds = 3600
    await h.work()
    assert (await h.sql("SELECT status FROM jobs WHERE run_id = :r", r=run))[0][0] == "running"
    await h.sql("UPDATE jobs SET heartbeat_at = now() - interval '1 hour'")
    h.settings.worker_stale_seconds = 60
    # only the remaining steps are scripted: explore and hypothesize are NOT re-run
    await h.work([PLAN, *implement_script()])
    row = (await h.client.get(f"/runs/{run}")).json()
    assert row["status"] == "finished" and row["termination_reason"] == "patch_proposed", row
    kinds = await h.sql("SELECT kind, status, attempts FROM jobs WHERE run_id = :r", r=run)
    assert kinds == [("continue", "done", 2)]
    events = await h.sql("SELECT event, node FROM events WHERE run_id = :r ORDER BY seq", r=run)
    assert ("run_continued", None) in [(e[0], e[1]) for e in events] or any(
        e[0] == "run_continued" for e in events
    )
    explores = [e for e in events if e[0] == "node_started" and e[1] == "explore"]
    assert len(explores) == 1


async def test_stale_job_gives_up_after_max_attempts(h: Harness, users_repo: Path) -> None:
    task = await h.task(users_repo)
    run = await h.run(task, approval="auto")
    await h.sql(
        "UPDATE jobs SET status = 'running', attempts = 3, heartbeat_at = now() - interval '1 hour'"
    )
    await h.work()
    row = (await h.client.get(f"/runs/{run}")).json()
    assert row["status"] == "failed" and "worker lost" in row["error"]
