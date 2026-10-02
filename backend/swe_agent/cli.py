"""Command-line entry point.

swe-agent run   --repo PATH|URL --issue TEXT [--env-file PROFILE]   # local, no database
swe-agent approve|reject|status RUN_ID
swe-agent bench validate [--tasks ID ...]            # check the benchmark itself (no model)
swe-agent bench run [--tasks ...] [--repeats N] [--out DIR]           # file-based results
swe-agent sandbox check                               # docker reachable, base image builds
swe-agent db upgrade                                  # apply database migrations
swe-agent api [--host H --port P]                     # HTTP API
swe-agent worker [--once]                             # executes queued runs
swe-agent experiment run CONFIG.toml [--enqueue-only] # systems x tasks x repeats
swe-agent experiment evaluate|report|list ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from swe_agent.agents.runner import run_task
from swe_agent.config import Settings, load_settings
from swe_agent.observability.logging import configure_logging


def _print_outcome(outcome: Any) -> int:
    if outcome.pending_approval is not None:
        req = outcome.pending_approval
        ver = req.get("verification") or {}
        print(f"\nAWAITING APPROVAL  run {outcome.run_id}  (task {outcome.task_id})")
        print(f"verdict: {ver.get('status')} (level {ver.get('level')})")
        for line in ver.get("evidence", []):
            print(f"evidence: {line}")
        for note in ver.get("notes", []):
            print(f"note: {note}")
        print(f"files changed: {', '.join(req.get('files_changed') or []) or '-'}")
        print(f"root cause: {req.get('root_cause') or '-'}")
        print(f"review: {req.get('patch_file')}")
        print(f"decide: swe-agent approve {outcome.run_id}")
        print(f"        swe-agent reject {outcome.run_id} [--feedback TEXT --retry]")
        return 3
    r = outcome.report
    trace = r.get("trace", {})
    ver = r.get("verification", {})
    print(
        f"\n{r.get('status')} (level {ver.get('level')})  "
        f"termination: {r.get('termination_reason')}"
    )
    print(f"task {outcome.task_id} / run {outcome.run_id}")
    print(f"files changed: {', '.join(r.get('files_changed') or []) or '-'}")
    print(f"root cause: {r.get('root_cause') or '-'}")
    if r.get("reproduction"):
        print(f"reproduction test: {r['reproduction']['test']}")
    print(f"iterations: {r.get('iterations')}  test runs: {trace.get('test_runs', 0)}  "
          f"rollbacks: {r.get('rollbacks', 0)}")  # fmt: skip
    print(
        f"tool calls: {trace.get('tool_calls')}  model calls: {trace.get('model_calls')}  "
        f"tokens in/out: {trace.get('tokens_in')}/{trace.get('tokens_out')}  "
        f"latency: {r.get('latency_s')}s"
    )
    for line in ver.get("evidence", []):
        print(f"evidence: {line}")
    for note in ver.get("notes", []):
        print(f"note: {note}")
    if "patch_applies_cleanly" in r:
        print(f"patch applies cleanly to base: {r['patch_applies_cleanly']}")
    print(f"artifacts: {outcome.artifacts_dir}")
    return 0 if r.get("termination_reason") == "patch_proposed" else 1


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    text = args.issue
    if args.issue_file:
        with open(args.issue_file, encoding="utf-8") as fh:
            text = fh.read()
    if args.auto_approve:
        settings.agent.approval = "auto"
    if args.acceptance:
        settings.agent.acceptance_dir = Path(args.acceptance)
    return _print_outcome(asyncio.run(run_task(args.repo, text, settings)))


def _cmd_decide(args: argparse.Namespace, settings: Settings) -> int:
    from swe_agent.agents.runner import RunNotFoundError, checkpoint_location, resume_run
    from swe_agent.schemas.state import ApprovalDecision

    decision = ApprovalDecision(
        approved=args.cmd == "approve",
        feedback=getattr(args, "feedback", None),
        retry=getattr(args, "retry", False),
        decided_by=getattr(args, "by", None) or "human",
    )
    try:
        outcome = asyncio.run(resume_run(args.run_id, decision, settings))
    except RunNotFoundError as exc:
        print(f"error: {exc} (checkpoints searched: {checkpoint_location(settings)})")
        return 2
    return _print_outcome(outcome)


def _cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    from swe_agent.agents.runner import RunNotFoundError, checkpoint_location, run_status

    try:
        print(json.dumps(asyncio.run(run_status(args.run_id, settings)), indent=2))
    except RunNotFoundError:
        print(f"error: no run {args.run_id} in {checkpoint_location(settings)}")
        return 2
    return 0


def _cmd_bench_validate(args: argparse.Namespace, settings: Settings) -> int:
    from swe_agent.evaluation.benchmark.evaluate import validate_task
    from swe_agent.evaluation.benchmark.tasks import load_tasks

    ok = True
    root = settings.benchmarks_dir.resolve()
    for task in load_tasks(root, only=args.tasks):
        v = validate_task(task, settings.sandbox, root)
        ok &= v.valid
        mark = "OK " if v.valid else "BAD"
        print(
            f"{mark} {task.id:<28} {task.category:<20} {task.difficulty:<6} "
            f"heldout failing at base {len(v.heldout_failing_at_base)}/{v.heldout_total}  "
            f"reference fix {v.reference_heldout_passed}/{v.heldout_total}  "
            f"visible failing at base {v.visible_failing_at_base or '-'}"
        )
        for p in v.problems:
            print(f"     problem: {p}")
    return 0 if ok else 1


def _cmd_bench_run(args: argparse.Namespace, settings: Settings) -> int:
    from swe_agent.evaluation.benchmark.run import run_benchmark, summarize

    rows = asyncio.run(
        run_benchmark(settings, Path(args.out), task_ids=args.tasks, repeats=args.repeats)
    )
    for r in rows:
        print(
            f"{'PASS' if r['success_l5'] else 'FAIL'} {r['task']:<28} rep {r['repeat']}  "
            f"held-out {r['heldout_passed']}/{r['heldout_total']}  "
            f"agent: {r['agent_status']} L{r['agent_level']} ({r['termination_reason']})  "
            f"iters {r['iterations']}  tools {r['tool_calls']}  wall {r['wall_s']}s"
        )
    print(json.dumps(summarize(rows), indent=2))
    print(f"results: {Path(args.out) / 'results.jsonl'}")
    return 0


def _cmd_sandbox_check(args: argparse.Namespace, settings: Settings) -> int:
    from swe_agent.sandbox.docker import EnvSetupError, docker_available
    from swe_agent.sandbox.image import ImageBuilder

    ok, info = docker_available(settings.sandbox.docker_bin)
    print(f"docker: {'ok ' + info if ok else 'UNAVAILABLE: ' + info}")
    if not ok:
        return 1
    try:
        built = ImageBuilder(settings.sandbox).ensure_base()
    except EnvSetupError as exc:
        print(f"base image: FAILED to build from {settings.sandbox.base_image}\n{str(exc)[-400:]}")
        print(
            "hint: if Docker Hub is unreachable, run scripts/build_local_base_image.sh and set "
            "SANDBOX_BASE_IMAGE / SANDBOX_PYTHON_VERSION / SANDBOX_MANYLINUX_MAX"
        )
        return 1
    print(
        f"base image: {built.tag} ({'cached' if built.cached else 'built'}) "
        f"from {settings.sandbox.base_image}"
    )
    return 0


def _need_db(settings: Settings) -> bool:
    if not settings.database_url:
        print("error: SWE_DATABASE_URL is not set (e.g. postgresql://swe:swe@localhost:5432/"
              "swe_agent); see .env.example")  # fmt: skip
        return False
    return True


def _cmd_db(args: argparse.Namespace, settings: Settings) -> int:
    if not _need_db(settings):
        return 2
    from swe_agent.agents.runner import setup_checkpoints
    from swe_agent.db.engine import upgrade

    upgrade(settings.database_url or "")
    asyncio.run(setup_checkpoints(settings))
    print("database schema is up to date (application tables, views, checkpoint tables)")
    return 0


def _cmd_api(args: argparse.Namespace, settings: Settings) -> int:
    if not _need_db(settings):
        return 2
    import uvicorn

    from swe_agent.main import create_app

    if args.host not in ("127.0.0.1", "localhost") and not settings.api_key:
        print("error: refusing to listen on a non-local address without SWE_API_KEY")
        return 2
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    if not _need_db(settings):
        return 2
    import signal

    from swe_agent.worker.service import Worker

    async def main() -> None:
        worker = Worker(settings)
        try:
            if args.once:
                while await worker.run_once():
                    pass
                return
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await worker.run_forever(stop)
        finally:
            await worker.aclose()

    asyncio.run(main())
    return 0


def _cmd_experiment(args: argparse.Namespace, settings: Settings) -> int:
    if not _need_db(settings):
        return 2
    from swe_agent.db.engine import make_async_engine, session_factory
    from swe_agent.evaluation.config import load_experiment
    from swe_agent.evaluation.metrics import experiment_tables
    from swe_agent.evaluation.report import CostAssumptions, generate_report
    from swe_agent.evaluation.runner import ExperimentRunner

    out = Path(args.out)

    async def tables(exp_id: str) -> dict[str, Any] | None:
        engine = make_async_engine(settings.database_url or "")
        try:
            async with session_factory(engine)() as s:
                return await experiment_tables(s, exp_id)
        finally:
            await engine.dispose()

    if args.exp_cmd == "run":
        config = load_experiment(args.config)

        async def run() -> str:
            runner = ExperimentRunner(settings, out_dir=out)
            try:
                return await runner.run(config, experiment_id=args.resume,
                                        execute=not args.enqueue_only)  # fmt: skip
            finally:
                await runner.aclose()

        exp_id = asyncio.run(run())
        print(f"experiment {exp_id}: "
              + ("runs enqueued for workers" if args.enqueue_only else "finished"))  # fmt: skip
        print(f"report: swe-agent experiment report {exp_id}")
        return 0
    if args.exp_cmd == "evaluate":

        async def ev() -> int:
            runner = ExperimentRunner(settings, out_dir=out)
            try:
                return await runner.evaluate_pending(args.experiment_id)
            finally:
                await runner.aclose()

        print(f"evaluated {asyncio.run(ev())} runs")
        return 0
    if args.exp_cmd == "list":
        from sqlalchemy import text as sql

        from swe_agent.db.engine import make_async_engine as mk

        async def lst() -> list[Any]:
            engine = mk(settings.database_url or "")
            try:
                async with session_factory(engine)() as s:
                    res = await s.execute(sql(
                        "SELECT e.id, e.name, e.status, e.created_at, count(r.id) AS runs "
                        "FROM experiments e LEFT JOIN agent_runs r ON r.experiment_id = e.id "
                        "GROUP BY e.id ORDER BY e.created_at DESC"))  # fmt: skip
                    return list(res)
            finally:
                await engine.dispose()

        for row in asyncio.run(lst()):
            print(f"{row.id}  {row.status:<9} runs {row.runs:<4} {row.created_at:%Y-%m-%d %H:%M}"
                  f"  {row.name}")  # fmt: skip
        return 0
    data = asyncio.run(tables(args.experiment_id))
    if data is None:
        print(f"error: no experiment {args.experiment_id}")
        return 2
    cost = CostAssumptions(args.usd_per_mtok_in, args.usd_per_mtok_out, args.gpu_usd_per_hour)
    path = generate_report(data, out / args.experiment_id / "report", cost)
    print(f"report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swe-agent")
    parser.add_argument("--env-file", help="settings profile, e.g. configs/models/ollama-dev.env")
    parser.add_argument("--pretty-logs", action="store_true", help="human-readable logs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the agent on one repository + issue")
    run.add_argument("--repo", required=True, help="local path or https git URL")
    issue = run.add_mutually_exclusive_group(required=True)
    issue.add_argument("--issue", help="issue text")
    issue.add_argument("--issue-file", help="file containing the issue text")
    run.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)
    run.add_argument(
        "--pretty-logs", dest="pretty_sub", action="store_true", help=argparse.SUPPRESS
    )
    run.add_argument("--auto-approve", action="store_true", help="skip the human approval pause")
    run.add_argument(
        "--acceptance", help="directory of acceptance tests (L5), never shown to the agent"
    )

    for name in ("approve", "reject"):
        d = sub.add_parser(name, help=f"{name} a run that is awaiting approval")
        d.add_argument("run_id")
        d.add_argument("--by", help="reviewer name recorded in the report")
        d.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)
        if name == "reject":
            d.add_argument("--feedback", help="what is wrong with the patch")
            d.add_argument("--retry", action="store_true", help="re-plan using the feedback")
    st = sub.add_parser("status", help="show a run's state")
    st.add_argument("run_id")
    st.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    bench = sub.add_parser("bench", help="development benchmark")
    bsub = bench.add_subparsers(dest="bench_cmd", required=True)
    val = bsub.add_parser("validate", help="check tasks with the reference fix (no model)")
    val.add_argument("--tasks", nargs="*")
    brun = bsub.add_parser("run", help="run the agent on the benchmark (real model)")
    brun.add_argument("--tasks", nargs="*")
    brun.add_argument("--repeats", type=int, default=1)
    brun.add_argument("--out", default=".data/bench")
    for p in (val, brun):
        p.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    sbx = sub.add_parser("sandbox", help="sandbox utilities")
    ssub = sbx.add_subparsers(dest="sandbox_cmd", required=True)
    chk = ssub.add_parser("check", help="verify docker + base image")
    chk.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    db = sub.add_parser("db", help="database schema")
    dsub = db.add_subparsers(dest="db_cmd", required=True)
    up = dsub.add_parser("upgrade", help="apply migrations")
    up.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    api = sub.add_parser("api", help="serve the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    wk = sub.add_parser("worker", help="execute queued runs")
    wk.add_argument("--once", action="store_true", help="drain the queue, then exit")
    wk.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    exp = sub.add_parser("experiment", help="evaluation experiments")
    esub = exp.add_subparsers(dest="exp_cmd", required=True)
    erun = esub.add_parser("run", help="run an experiment config (configs/experiments/*.toml)")
    erun.add_argument("config")
    erun.add_argument("--enqueue-only", action="store_true",
                      help="only enqueue runs for `swe-agent worker`; evaluate later")  # fmt: skip
    erun.add_argument("--resume", metavar="EXPERIMENT_ID", help="continue an experiment")
    eev = esub.add_parser("evaluate", help="score finished runs that have no evaluation yet")
    eev.add_argument("experiment_id")
    erep = esub.add_parser("report", help="write report.md / CSVs / chart from the database")
    erep.add_argument("experiment_id")
    erep.add_argument("--usd-per-mtok-in", type=float, default=0.0)
    erep.add_argument("--usd-per-mtok-out", type=float, default=0.0)
    erep.add_argument("--gpu-usd-per-hour", type=float, default=0.0)
    elist = esub.add_parser("list", help="list experiments")
    for p in (erun, eev, erep, elist):
        p.add_argument("--out", default=".data/experiments")
        p.add_argument("--env-file", dest="env_file_sub", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    env_file = getattr(args, "env_file_sub", None) or args.env_file
    settings = load_settings(env_file)
    configure_logging(
        settings.log_level, json=not (args.pretty_logs or getattr(args, "pretty_sub", False))
    )

    if args.cmd == "run":
        return _cmd_run(args, settings)
    if args.cmd in ("approve", "reject"):
        return _cmd_decide(args, settings)
    if args.cmd == "status":
        return _cmd_status(args, settings)
    if args.cmd == "db":
        return _cmd_db(args, settings)
    if args.cmd == "api":
        return _cmd_api(args, settings)
    if args.cmd == "worker":
        return _cmd_worker(args, settings)
    if args.cmd == "experiment":
        return _cmd_experiment(args, settings)
    if args.cmd == "bench":
        return (_cmd_bench_validate if args.bench_cmd == "validate" else _cmd_bench_run)(
            args, settings
        )
    return _cmd_sandbox_check(args, settings)


if __name__ == "__main__":
    sys.exit(main())
