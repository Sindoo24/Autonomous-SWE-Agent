# Autonomous Software Engineering Agent

An autonomous agent that takes a **Python repository and a bug report** and returns a **minimal patch with machine-checked evidence** that the patch works. It explores the code, forms hypotheses, plans, writes a failing reproduction test, edits the code, runs the tests in a locked-down Docker sandbox, recovers from its own failures, and waits for a human to approve the patch before anything leaves its workspace.

It runs on **open-source models** (Ollama or any OpenAI-compatible server such as vLLM), is built as an explicit **LangGraph** state machine, and comes with a FastAPI backend, a PostgreSQL job queue, OpenTelemetry tracing, a 25-task benchmark with held-out tests, two baseline systems for comparison, and a Streamlit UI.

> **Project status: Phase 8 of 10.** Everything below is implemented and covered by 238 automated tests (scripted model, real Docker sandbox, real PostgreSQL).
>
> **No benchmark results from a real model are published yet.** The numbers will come from `swe-agent experiment report`, which generates them from the database. Nothing in this README is a performance claim.

---

## Contents

- [Why this project](#why-this-project)
- [How a run works](#how-a-run-works)
- [Verification levels](#verification-levels)
- [System architecture](#system-architecture)
- [Security model](#security-model)
- [Benchmark and evaluation](#benchmark-and-evaluation)
- [Quick start](#quick-start)
- [Running the backend, worker and UI](#running-the-backend-worker-and-ui)
- [Running experiments](#running-experiments)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [What a run produces](#what-a-run-produces)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Tech stack](#tech-stack)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [Documentation](#documentation)
- [License](#license)

---

## Why this project

Most "AI fixes your bug" demos stop once the model has produced a diff. This project treats that as the start, and the question that matters is **"how do we know the patch is right?"**:

- **Evidence, not claims.** The verdict (VERIFIED / NOT VERIFIED, level 1–5) is computed only from sandboxed test runs and static checks, never from what the model says.
- **Fail → pass proof.** If no existing test covers the bug, the agent first writes a test, and it is accepted only if it **fails on the unpatched code for the right reason**.
- **Closed-loop recovery.** Test failures are classified. Repeated failures escalate from re-implementing, to re-planning, to re-exploring from a clean checkout, to stopping. Identical repeated patches are detected.
- **Safety by construction.** Untrusted code only runs in a hardened container. The model can only act through typed tools with path, write and budget policies. Tests, CI and build files are read-only to the model.
- **Honest evaluation.** A 25-task benchmark with held-out tests, two baselines, four ablations, hypotheses written down before any run, and metrics defined as SQL views.

---

## How a run works

```mermaid
flowchart TD
    A[intake] --> B[prepare_repo]
    B --> C[baseline_tests]
    C --> D[explore]
    D --> E[hypothesize]
    E --> F[plan]
    F --> G[reproduce]
    G --> H[implement]
    H --> I[validate_patch]
    I -- rejected, attempts left --> H
    I -- accepted --> J[run_tests]
    J -- pass --> K[verify]
    J -- fail --> L[analyze_failure]
    L -- syntax / import error --> H
    L -- test failure / regression --> F
    L -- same failure again / wrong place --> D
    L -- unrecoverable / budget / oscillation --> Z[report_failure]
    K --> M[human_approval]
    M -- approved --> N[finalize]
    M -- rejected + feedback + retry --> F
    M -- rejected --> Z
```

| Stage | Kind | What it does |
|---|---|---|
| `intake` | deterministic | Sanitises and validates the issue (control characters, empty, too long) |
| `prepare_repo` | deterministic | Hardened clone on branch `agent/<task_id>` (hooks, fsmonitor and textconv disabled). Builds a tree-sitter symbol index and a ranked list of candidate files |
| `baseline_tests` | sandbox | Builds the dependency image and runs the suite on the **unpatched** code. Records which tests already fail |
| `explore` | LLM tool loop, read-only | Finds the responsible code. Every cited file and line range is checked to exist |
| `hypothesize` / `plan` | LLM, structured output | Root-cause hypotheses with evidence, then a typed plan (files, changes, tests, risks, rollback) |
| `reproduce` | LLM + sandbox | Writes `tests/test_swe_agent_repro.py`. Accepted only if it fails on the unpatched code for the right reason: an assertion or the bug's exception, not ImportError, NameError or a collection error. Up to 3 attempts with pytest feedback |
| `implement` | LLM tool loop | Exact search/replace edits. Tests, CI and build files are read-only |
| `validate_patch` | deterministic | Path policy, `ast` syntax, size limit, test-tampering patterns (skip/xfail/`sys.exit`), deviation from the plan |
| `run_tests` | sandbox | Import check of changed modules, then the full suite on a disposable snapshot. Results come from JUnit XML |
| `analyze_failure` | rules + LLM | Classifies the failure (syntax, import, missing dependency, test failure, regression, timeout, resource limit, sandbox error). For test failures, a model-written root-cause analysis can send the run back to exploration. Detects stagnation and oscillation. Rolls the workspace back before re-exploring |
| `verify` | deterministic + sandbox | Computes the verification level (below) |
| `human_approval` | human | Pauses the run as a durable checkpoint. Approve or reject from the UI, the API or the CLI, from any process |
| `finalize` / `report_failure` | deterministic | Writes `report.json`, `final.patch` and the trajectory, and checks that the patch applies cleanly to the original commit |

---

## Verification levels

Computed **only** from test runs and static checks:

| Level | Meaning |
|---|---|
| **L1** | Changed files parse, and changed modules import inside the sandbox |
| **L2** | L1, plus a test that **failed before** the patch **passes after** it (an existing test or the agent's reproduction test) |
| **L3** | L2, plus **no regressions**: every test that passed before still passes |
| **L4** | L3, plus **no new lint findings** (ruff `E9`/`F` on changed files, compared with the same files before the patch) |
| **L5** | L4, plus user-supplied **acceptance tests** the agent never saw (`--acceptance DIR`) |

A run is **VERIFIED** only at L3 or above.

---

## System architecture

```mermaid
flowchart LR
    UI[Streamlit UI<br/>frontend/] -->|HTTP| API[FastAPI<br/>swe_agent.api]
    API --> DB[(PostgreSQL<br/>tasks · runs · jobs · events<br/>metric views · checkpoints)]
    W[Worker<br/>swe_agent.worker] -->|claim job<br/>SKIP LOCKED| DB
    W --> G[LangGraph agent<br/>swe_agent.graph]
    G -->|checkpoints| DB
    G -->|events + spans| DB
    G --> LLM[Model provider<br/>Ollama / vLLM / OpenAI-compatible]
    G --> T[Typed tools<br/>read · search · edit · git]
    G --> S[Docker sandbox<br/>no network · read-only · uid 10001]
```

- **The API never runs the agent.** It records tasks and runs and enqueues jobs. Workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED`, so any number of workers can run without a message broker.
- **Durable runs.** Every node transition is checkpointed (LangGraph `AsyncPostgresSaver`). If a worker dies mid-run, its job is requeued after a missed heartbeat, and another worker **continues from the last checkpoint** instead of starting over.
- **One source of truth for metrics.** Every model call, tool call and sandbox run is a trajectory event, saved to `trajectory.jsonl` and to the `events` table. Latency, token, tool-usage and evaluation metrics are **SQL views** over those events, and OpenTelemetry spans are derived from the same events.
- **Model-agnostic.** A provider abstraction supports Ollama and OpenAI-compatible servers, with two tool-calling modes: native tools, or a JSON action schema for small models.

Design decisions are recorded in [`docs/architecture.md`](docs/architecture.md) and [`docs/adr/`](docs/adr/).

---

## Security model

Everything that executes repository or model-generated code runs in Docker with:

```
--network none  --user 10001  --read-only  --tmpfs /tmp  --cap-drop ALL
--security-opt no-new-privileges  --pids-limit  --memory/--memory-swap  --cpus
--ulimit nofile  --init
```

On top of those flags:

- **Disposable snapshot.** Only a snapshot of the workspace is mounted: regular files inside the workspace, no `.git`, and symlinks pointing outside are dropped. Code inside the container can never modify the agent's workspace or diff.
- **No host secrets.** The container receives an explicit environment allow-list; no host variables are passed through.
- **Fixed commands.** Only pytest, an import check or ruff runs, as a fixed argv. Test selections are validated node ids.
- **Offline dependency install.** The host downloads **wheels only** (no package code runs on the host). The image is built with `docker build --network none` and cached by content hash.
- **Cleanup.** Timeouts kill the container, and containers are always removed. A reaper removes leftovers from crashed workers.
- **Model confinement.** The model acts only through typed tools, under a path jail, a per-node write policy (tests, CI and build files are read-only) and a budget. Repository content is passed to the model as clearly marked untrusted data, and injection attempts are flagged in the trajectory.

Measured inside the container in the test suite: uid 10001, zero capabilities, network blocked, read-only root filesystem, no Docker socket, no host secrets. The memory limit OOM-kills, and the pid limit stops a fork bomb.

---

## Benchmark and evaluation

**25 tasks** across six purpose-built repositories (`users_api`, `datakit`, `algokit`, `ledger_cli`, `inventory_api`, `eventlog`). Each task is a clean repository plus a `bug.patch` that injects the bug, with **held-out tests the agent never sees**.

| | Easy | Medium | Hard | Total |
|---|---|---|---|---|
| Tasks | 10 | 10 | 5 | **25** |

| Category | Count |
|---|---|
| Algorithmic | 4 |
| Logic | 4 |
| Regression | 4 |
| Edge case | 4 |
| API | 3 |
| Validation | 3 |
| Data processing | 3 |

- **Hard tasks** have a non-local cause: the symptom appears in one module and the fault is in another.
- **Regression tasks** inject the bug as its own commit, so `git log` is informative.
- **Validated.** `swe-agent bench validate` checks every task: the held-out tests fail on the buggy code and pass with the reference fix, and the visible suite is green. All 25 are valid.

**Systems compared.** They share the same model, sandbox, write policy and budget:

| System | Description |
|---|---|
| **C. Agent** | The full closed loop above |
| **B. ReAct baseline** | One tool loop with every tool, including `run_tests`, until it says "done". No plan, reproduction, failure classifier or verification gate |
| **A. Single-shot baseline** | The agent's own file ranker picks the top 5 files. One model call returns edits. No tests, no retry |

**Ablations of the agent:** `no_reproduce`, `generic_retry` (no failure classifier), `no_candidate_seeding`, `single_iteration`.

**Metrics**, all defined as SQL views:
- held-out success rate, with min–max across repeats
- first-attempt success
- held-out test-pass rate
- recovery rate
- iterations
- latency p50/p90 with a model/tool/sandbox breakdown
- tokens (never estimated)
- cost estimate, with stated assumptions
- tool calls and repeated calls
- plan deviation rate
- test-tampering attempts

The hypotheses (H1–H4) were **pre-registered** in [`docs/evaluation.md`](docs/evaluation.md) before any run.

---

## Quick start

### Requirements

- Python **3.11+**, `git`, `ripgrep`
- **Docker** (for the sandbox; set `SANDBOX_ENABLED=false` to generate patches without executing anything)
- A model server: [Ollama](https://ollama.com) locally, or any OpenAI-compatible endpoint (vLLM, etc.)
- **PostgreSQL 14+**, only for the API, worker, UI and experiments

> **Windows:** use WSL2 + Docker Desktop + Ollama for Windows. The step-by-step guide, including a low-VRAM (6 GB GPU) profile, is in [`docs/windows-setup.md`](docs/windows-setup.md).

### Install

```bash
git clone https://github.com/<your-username>/autonomous-swe-agent.git
cd autonomous-swe-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ui,charts]"
```

### Check the environment (no model needed)

```bash
swe-agent sandbox check         # Docker reachable, sandbox base image builds
swe-agent bench validate        # all 25 benchmark tasks validated with their reference fixes
pytest -q                       # full test suite (Docker / PostgreSQL tests skip if unavailable)
```

### Fix a bug from the command line

```bash
ollama pull qwen2.5-coder:7b

swe-agent --env-file configs/models/ollama-small.env run \
  --repo tests/fixtures/repos/users_service \
  --issue "POST /users returns HTTP 500 when the email field is missing. It should return 422."
```

The run pauses for approval and prints `AWAITING APPROVAL run r_…` with the verdict, evidence and the path of the pending patch. Then:

```bash
swe-agent --env-file configs/models/ollama-small.env approve r_…                     # export final.patch
swe-agent --env-file configs/models/ollama-small.env reject  r_… --feedback "…" --retry   # re-plan
swe-agent --env-file configs/models/ollama-small.env status  r_…
```

Use `--auto-approve` to skip the pause. Apply the result to your own repository with `git apply final.patch`.

---

## Running the backend, worker and UI

```bash
docker compose up -d postgres                              # PostgreSQL 16 on 127.0.0.1:5432
export SWE_DATABASE_URL=postgresql://swe:swe@localhost:5432/swe_agent
swe-agent db upgrade                                       # migrations + checkpoint tables

# three terminals:
swe-agent --env-file configs/models/ollama-small.env api      # http://127.0.0.1:8000/docs
swe-agent --env-file configs/models/ollama-small.env worker   # executes queued runs
streamlit run frontend/app.py                                 # http://localhost:8501
```

The **UI** lets you:
- submit a repository and an issue
- watch the run live: current node, budget, node timeline
- inspect the plan and hypotheses, every tool and model call, each patch attempt with its diff, the test runs and reproduction test, the final diff, and latency/token metrics
- **approve or reject** the patch (with feedback for a retry), or cancel the run
- browse experiment results

**Main API endpoints** (full list in [`docs/backend.md`](docs/backend.md)):

| Method | Path | Purpose |
|---|---|---|
| POST | `/tasks` | Create a task `{repo, issue}` |
| POST | `/tasks/{id}/runs` | Enqueue a run `{system, approval, overrides}` (idempotency key supported) |
| GET | `/runs/{id}/status` | Status, current node, budget |
| GET | `/runs/{id}/trajectory?after_seq=` | Paged events |
| GET | `/runs/{id}/plan` · `/patches` · `/tests` · `/diff` · `/report` · `/metrics` | Run details |
| POST | `/runs/{id}/approve` · `/reject` · `/cancel` | Human decisions |
| GET | `/experiments`, `/experiments/{id}/metrics` | Evaluation results |

Optional tracing: `docker compose --profile jaeger up -d`, set `SWE_OTEL_EXPORTER=otlp`, install with `pip install -e ".[otlp]"`, and open http://localhost:16686.

---

## Running experiments

```bash
swe-agent --env-file configs/models/ollama-small.env experiment run configs/experiments/smoke.toml
swe-agent experiment list
swe-agent experiment report EXP_ID        # report.md + CSVs + chart, generated from SQL views
```

| Config | Runs | Purpose |
|---|---|---|
| `smoke.toml` | 9 | 3 easy tasks × 3 systems: checks the whole pipeline |
| `main.toml` | 225 | 25 tasks × 3 systems × 3 seeds |
| `ablations.toml` | 375 | 25 tasks × 5 agent variants × 3 seeds |

`--resume EXP_ID` continues an interrupted experiment. `--enqueue-only` lets several `swe-agent worker` processes share the load. Runs made with the scripted test provider are refused unless explicitly allowed, and are labelled **"NOT model performance"** everywhere.

---

## CLI reference

| Command | What it does |
|---|---|
| `swe-agent run --repo PATH\|URL --issue TEXT [--auto-approve] [--acceptance DIR]` | Run the agent locally (no database needed) |
| `swe-agent approve\|reject\|status RUN_ID` | Decide on, or inspect, a paused run |
| `swe-agent sandbox check` | Docker and base-image check |
| `swe-agent bench validate [--tasks …]` | Validate benchmark tasks with their reference fixes |
| `swe-agent bench run [--tasks …] [--repeats N]` | File-based benchmark run (no database) |
| `swe-agent db upgrade` | Apply database migrations |
| `swe-agent api [--host --port]` | Start the HTTP API |
| `swe-agent worker [--once]` | Execute queued runs |
| `swe-agent experiment run\|evaluate\|report\|list` | Evaluation experiments |

Every command accepts `--env-file <profile>`.

---

## Configuration

All settings come from environment variables, optionally loaded from a profile with `--env-file`. Variables exported in the shell take precedence.

| Prefix | Examples | Purpose |
|---|---|---|
| `MODEL_` | `PROVIDER`, `BASE_URL`, `REASONING`, `CODER`, `TOOL_MODE`, `NUM_CTX`, `THINK` | Model server and models |
| `BUDGET_` | `MAX_ITERATIONS`, `MAX_TOOL_CALLS`, `MAX_TOKENS`, `MAX_WALL_SECONDS` | Hard limits per run |
| `SANDBOX_` | `ENABLED`, `BASE_IMAGE`, `MEMORY`, `CPUS`, `TEST_TIMEOUT_S`, `RUNTIME` | Execution sandbox |
| `AGENT_` | `REPRODUCE`, `LLM_FAILURE_ANALYSIS`, `APPROVAL`, `LINT`, `ACCEPTANCE_DIR` | Behaviour switches |
| `SWE_` | `DATABASE_URL`, `API_KEY`, `API_REPO_ROOTS`, `OTEL_EXPORTER`, `ARTIFACTS_ROOT` | Backend and storage |

Ready-made profiles:
- [`configs/models/ollama-small.env`](configs/models/ollama-small.env): one 7B model, 8 GB GPU or less
- [`configs/models/ollama-dev.env`](configs/models/ollama-dev.env): two models
- [`configs/models/vllm-gpu.env`](configs/models/vllm-gpu.env): an OpenAI-compatible vLLM server

Backend settings are in [`configs/backend.env.example`](configs/backend.env.example), and a full annotated list is in [`.env.example`](.env.example).

---

## What a run produces

Each run writes to `.data/artifacts/<task_id>/<run_id>/`:

| File | Contents |
|---|---|
| `report.json` | Verdict and level with evidence, root cause, plan, every patch attempt, baseline, reproduction test, failure history, root-cause analyses, approval, test runs, budget, trace summary |
| `final.patch` | Unified diff against the original commit, checked with `git apply --check` |
| `trajectory.jsonl` | One event per node transition, tool call, model call, sandbox run, recovery decision and approval |
| `spans.jsonl` | OpenTelemetry spans: one trace per run |
| `approval_request.json`, `pending.patch` | What the reviewer sees while the run is paused |
| `art_*` | Prompts, model responses, tool outputs, test logs and JUnit XML, referenced from the trajectory |

---

## Project structure

```
autonomous-swe-agent/
├── src/swe_agent/
│   ├── graph/            # LangGraph builder, state, tool loop, one module per group of nodes
│   ├── llm/              # provider protocol, Ollama + OpenAI-compatible clients, gateway, scripted provider
│   ├── tools/            # typed tools (read/search/outline/git/edit/run_tests) + executor
│   ├── codeintel/        # tree-sitter symbol index, ripgrep search, candidate ranking
│   ├── workspace/        # hardened clone, path jail, write policies, git helpers
│   ├── sandbox/          # Docker runner, image builder, JUnit parsing, lint
│   ├── verification/     # patch validator, failure classifier, verification levels
│   ├── prompts/          # versioned prompt templates, prompt-injection defences
│   ├── observability/    # structured logs, trajectory recorder, artifacts, OpenTelemetry
│   ├── db/               # SQLAlchemy models, Alembic migrations, data access, event sink
│   ├── api/              # FastAPI app
│   ├── worker/           # job-queue worker
│   ├── baselines/        # single-shot (A) and ReAct (B) baselines
│   ├── evaluation/       # experiment runner, SQL metrics, report generator
│   ├── benchmark/        # benchmark task loading, materialisation, held-out evaluation
│   ├── runner.py         # run / resume / continue a run
│   └── cli.py            # `swe-agent` command
├── frontend/             # Streamlit UI + API client
├── benchmarks/           # 6 repositories, 25 tasks (bug.patch + held-out tests)
├── configs/              # model profiles, experiment configs, backend env example
├── docs/                 # architecture, backend, evaluation, setup guide, phase reports, ADRs
├── scripts/              # local base-image builder, results collector
├── tests/                # unit · integration · security · sandbox · backend · live
└── docker-compose.yml    # PostgreSQL (+ optional Jaeger)
```

---

## Testing

```bash
pytest -q                                   # everything available on this machine
pytest -q tests/unit tests/integration      # fast, no Docker / database
```

| Suite | Covers |
|---|---|
| `tests/unit` | Path jail, workspace, code intelligence, tools, providers, gateway, patch validator, sandbox argv, JUnit parsing, failure classifier, verification levels, configs, report rendering |
| `tests/integration` | Full graph with a scripted model; CLI against real HTTP model servers; OpenTelemetry spans |
| `tests/security` | Git hooks, fsmonitor, textconv, `ext::`; symlink escapes; prompt injection; a "hijacked" model confined by its tools |
| `tests/sandbox` (Docker) | Isolation (network, filesystem, env, uid, capabilities), limits (time, memory, pids, output), the full graph with real test execution, recovery paths, approval across processes, the benchmark harness, the experiment runner with all three systems |
| `tests/backend` (PostgreSQL) | API validation and auth, run lifecycle through the worker, approve/reject/cancel, **crash recovery from checkpoints**, metric views, the Streamlit UI driven through AppTest |
| `tests/live` | Real model (opt-in: `SWE_LIVE_MODEL=1`) |

**Status:**
- **238 passed, 1 skipped** (the live-model test); coverage 90%; `ruff` and `mypy --strict` clean.
- **Docker tests** skip automatically without a Docker daemon.
- **PostgreSQL tests** skip without `SWE_TEST_DATABASE_URL` (default `postgresql://postgres@127.0.0.1:5432/swe_agent_test`). **That database is wiped on every run**, so point it at a disposable database.

---

## Tech stack

| Area | Choice |
|---|---|
| Orchestration | LangGraph (explicit state machine, conditional edges, `interrupt()`, checkpoints) |
| Models | Ollama, vLLM / any OpenAI-compatible server; JSON-schema-constrained tool calls |
| Schemas | Pydantic v2, pydantic-settings |
| Code understanding | tree-sitter, ripgrep |
| Execution | Docker (hardened), pytest + JUnit XML, ruff |
| Backend | FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL, psycopg 3 |
| Observability | structlog, OpenTelemetry, SQL views |
| UI | Streamlit |
| Quality | pytest, ruff, mypy (strict) |

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Graph, model abstraction, tools, planner, patch generation, trajectory | ✅ |
| 2 | Docker sandbox, baseline tests, failure classifier, execution loop, first benchmark | ✅ |
| 3 | Reproduction tests, LLM root-cause analysis, stagnation escalation, rollback | ✅ |
| 4 | L4/L5 verification, human approval with durable checkpoints, patch export | ✅ |
| 5 | FastAPI, PostgreSQL, job queue + worker, crash recovery, cancel | ✅ |
| 6 | OpenTelemetry spans, latency / token / tool-usage SQL views | ✅ |
| 7 | 25-task benchmark, baselines, ablations, experiment runner, report generator | ✅ harness; real-model results pending |
| 8 | Streamlit UI | ✅ |
| 9 | Containerised deployment (api / worker / UI), vLLM profile, GPU benchmark run | ⏳ |
| 10 | Final documentation with real benchmark results | ⏳ |

---

## Limitations

- **No real-model results yet.** One early live run with `qwen2.5-coder:7b` exposed an edit-tool failure mode, which was fixed. Otherwise the system has been exercised with a scripted model.
- **Small, synthetic benchmark.** All 25 tasks are purpose-built and uncontaminated by model training data, but small. Results will describe this benchmark, not bug-fixing in general.
- **Python repositories with pytest suites only.** Dependencies that ship only as source distributions (no wheels) cannot be installed in the offline sandbox build.
- **Single machine.** The API, worker and sandboxes must share filesystem paths. Multi-host deployment is Phase 9.
- **Static API key.** No user accounts. Meant as a local tool.
- **Heuristic reproduction-test acceptance.** The check that a reproduction test fails "for the right reason" is heuristic. The held-out tests are the safeguard against a wrong test.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Full design: requirements, graph, state, tools, models, sandbox, verification, database, API, evaluation, risks, plus implementation notes per phase |
| [`docs/backend.md`](docs/backend.md) | API, worker, job lifecycle, crash recovery, observability |
| [`docs/evaluation.md`](docs/evaluation.md) | Benchmark, systems, metric definitions, pre-registered hypotheses |
| [`docs/windows-setup.md`](docs/windows-setup.md) | Step-by-step setup on Windows (WSL2 + Docker Desktop + Ollama) |
| [`docs/phase-2-report.md`](docs/phase-2-report.md), [`docs/phase-3-4-report.md`](docs/phase-3-4-report.md), [`docs/phase-5-8-report.md`](docs/phase-5-8-report.md) | What each phase built, how it was tested, deviations, limitations |
| [`docs/adr/`](docs/adr/) | Architecture decision records |
| [`CHANGELOG.md`](CHANGELOG.md) | Changes by phase |

---

## License

[MIT](LICENSE)
