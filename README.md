# Autonomous Software Engineering Agent

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-1C3C3C)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/database-PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/sandbox-Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

An autonomous agent that takes a Python repository and a bug report, and returns a minimal patch together with machine-checked evidence that the patch works.

---

## Overview

Most LLM coding tools stop once the model has produced a diff. This project is built around the question that comes next: **how do we know the patch is correct?**

Given a repository and an issue description, the agent:

1. **Explores** the codebase with read-only tools and identifies the code responsible for the bug.
2. **Hypothesizes** about the root cause and produces a structured plan.
3. **Reproduces** the bug by writing a test that must fail on the unpatched code.
4. **Implements** a minimal fix with exact search-and-replace edits.
5. **Verifies** the fix by running the test suite in an isolated Docker sandbox.
6. **Recovers** from failures by classifying them and re-planning, re-exploring or stopping.
7. **Waits for human approval** before exporting the patch.

The verdict on every patch is computed from test results, never from the model's own claims. The agent runs on locally hosted models (Ollama or any OpenAI-compatible server such as vLLM). By default it uses `qwen2.5-coder:7b`, which fits on a 6 GB GPU.

### Key features

| Area | Capability |
|---|---|
| Correctness | Fail-to-pass evidence, regression detection and lint checks, combined into five verification levels |
| Recovery | Failure classification, root-cause analysis, escalation, rollback, oscillation detection |
| Safety | Hardened Docker sandbox; tools restricted per stage; tests and CI files read-only to the model |
| Human oversight | Durable pause before export; approve, reject or request a retry with feedback |
| Backend | FastAPI service, PostgreSQL job queue, workers that resume a crashed run from its checkpoint |
| Observability | Full trajectory of every step, OpenTelemetry traces, metrics as SQL views |
| Evaluation | 25-task benchmark with hidden tests, two baseline systems, four ablations |
| Interface | CLI, REST API and a Streamlit web UI |

---

## Architecture

```mermaid
flowchart LR
    UI["Streamlit UI"] -->|HTTP| API["FastAPI service"]
    CLI["CLI"] --> AG
    API --> DB[("PostgreSQL<br/>tasks · runs · jobs<br/>events · checkpoints")]
    W["Worker(s)"] -->|"claim job<br/>(SKIP LOCKED)"| DB
    W --> AG["LangGraph agent"]
    AG -->|"checkpoints, events"| DB
    AG --> LLM["Model server<br/>Ollama / vLLM"]
    AG --> TOOLS["Typed tools<br/>read · search · edit · git"]
    AG --> SB["Docker sandbox<br/>pytest · ruff"]
```

| Component | Responsibility |
|---|---|
| **LangGraph agent** (`graph/`) | Explicit state machine of 15 nodes with conditional routing; every transition is checkpointed |
| **Model gateway** (`llm/`) | Provider abstraction for Ollama and OpenAI-compatible APIs; schema-constrained structured output with one repair retry |
| **Tools** (`tools/`) | Typed, policy-checked tools: file reading, code search, symbol lookup, git history, exact-match editing |
| **Sandbox** (`sandbox/`) | Runs tests in containers with no network, a read-only filesystem, an unprivileged user and resource limits |
| **Verification** (`verification/`) | Patch validation, failure classification and computation of the verification level |
| **API and worker** (`api/`, `worker/`) | The API records tasks and enqueues jobs; workers claim jobs, execute runs and heartbeat |
| **Persistence** (`db/`) | PostgreSQL schema with Alembic migrations, the event store, and metric views |
| **Evaluation** (`evaluation/`, `baselines/`) | Experiment runner, baseline systems, SQL metrics and report generation |

### Agent workflow

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
    I -- rejected --> H
    I -- accepted --> J[run_tests]
    J -- pass --> K[verify]
    J -- fail --> L[analyze_failure]
    L -- code error --> H
    L -- wrong approach --> F
    L -- wrong location --> D
    L -- unrecoverable --> Z[report_failure]
    K --> M[human_approval]
    M -- approved --> N[finalize]
    M -- rejected with feedback --> F
    M -- rejected --> Z
```

| Stage | Description |
|---|---|
| `prepare_repo` | Clones the repository into an isolated workspace with git hooks disabled; builds a symbol index and ranks candidate files |
| `baseline_tests` | Runs the existing tests on unmodified code to record which tests already fail |
| `explore` | Read-only tool loop; every file and line range the model cites is checked to exist |
| `hypothesize`, `plan` | Root-cause hypotheses backed by evidence, then a typed plan of changes |
| `reproduce` | Writes a test that must fail on the unpatched code for the reason the issue describes |
| `implement` | Applies exact search-and-replace edits; test files are read-only |
| `validate_patch` | Checks syntax, patch size, test-tampering patterns and deviation from the plan |
| `run_tests` | Runs the full test suite on a disposable snapshot inside the sandbox |
| `analyze_failure` | Classifies the failure and routes the run back to implement, plan or explore, or stops it |
| `human_approval` | Pauses the run durably until a reviewer approves or rejects the patch |

---

## Verification levels

Each patch receives a level computed only from test runs and static checks.

| Level | Requirement |
|:---:|---|
| **L1** | The changed files parse, and the changed modules import inside the sandbox |
| **L2** | L1, and a test that **failed before** the patch **passes after** it |
| **L3** | L2, and **no regressions**: every test that passed before still passes |
| **L4** | L3, and **no new lint findings** in the changed files (ruff) |
| **L5** | L4, and user-supplied **acceptance tests**, never shown to the agent, pass |

A patch is reported as **VERIFIED** only at L3 or above. A run that cannot reproduce the bug is reported honestly as NOT VERIFIED, even if a patch was produced.

---

## Evaluation

### Benchmark

25 tasks across six purpose-built Python repositories. Each task injects a bug into a clean repository and includes **held-out tests that the agent never sees**. A run succeeds only if every held-out test passes and no existing test breaks on a fresh copy of the repository. Every task has been validated: its held-out tests fail on the buggy code and pass with the reference fix.

| Difficulty | Tasks |
|---|:---:|
| Easy | 10 |
| Medium | 10 |
| Hard (cause in a different module from the symptom) | 5 |

| Category | Tasks |
|---|:---:|
| Algorithmic, logic, regression, edge case | 4 each |
| API, validation, data processing | 3 each |

### Systems compared

All systems use the same model, sandbox, tool permissions and budget.

| System | Description |
|---|---|
| **Agent** | The full workflow described above |
| **ReAct baseline** | A single free-form tool loop that can also run tests; no plan, reproduction or verification stage |
| **Single-shot baseline** | The five most relevant files are retrieved and the model proposes a patch in one call |

Ablations of the agent remove one component at a time: the reproduction step, failure classification, candidate-file ranking, and multiple iterations.

### Metrics

Every metric is defined as a SQL view over the recorded runs, so each reported number can be traced back to individual events.

| Metric | Definition |
|---|---|
| Task success rate | Runs whose final patch passes all held-out tests without regressions, divided by runs |
| First-attempt success | Runs whose first patch already succeeds |
| Held-out test pass rate | Held-out tests passed divided by held-out tests, across runs (partial credit) |
| Recovery rate | Among runs whose first patch failed, the fraction that eventually succeeded |
| Iterations | Mean number of patch iterations per run |
| Latency | Median and 90th-percentile wall-clock time, split into model, tool and sandbox time |
| Token usage | Input and output tokens as reported by the model server; never estimated |
| Estimated cost | Tokens and GPU time multiplied by stated prices, with the assumptions shown |
| Tool usage | Tool calls per run, by tool, including repeated identical calls |
| Plan deviation rate | Share of patches that change files or symbols outside the plan |
| Tamper attempts | Attempts to skip, weaken or edit tests, which are blocked and counted |

Each configuration is run three times with different seeds, and the spread across repeats is reported. The hypotheses being tested were written down before any experiment ([`docs/evaluation.md`](docs/evaluation.md)).

---

## Quick start

### Prerequisites

- Python 3.11 or later, `git` and `ripgrep`
- Docker
- [Ollama](https://ollama.com) (or another OpenAI-compatible model server)
- PostgreSQL 14 or later, only for the API, worker and web UI

On Windows, use WSL2. A step-by-step guide is in [`docs/windows-setup.md`](docs/windows-setup.md).

### 1. Install

```bash
git clone https://github.com/<your-username>/autonomous-swe-agent.git
cd autonomous-swe-agent
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ui,charts]"
```

### 2. Pull the model

```bash
ollama pull qwen2.5-coder:7b
```

### 3. Verify the setup

```bash
swe-agent sandbox check      # Docker is reachable and the sandbox image builds
swe-agent bench validate     # all 25 benchmark tasks are valid
pytest -q                    # run the test suite
```

### 4. Fix a bug

```bash
swe-agent --env-file configs/models/ollama-small.env run \
  --repo tests/fixtures/repos/users_service \
  --issue "POST /users returns HTTP 500 when the email field is missing. It should return 422."
```

The run stops before exporting anything and prints its verdict and evidence. To approve, or to reject with feedback and let the agent try again:

```bash
swe-agent --env-file configs/models/ollama-small.env approve <run_id>
swe-agent --env-file configs/models/ollama-small.env reject <run_id> --feedback "..." --retry
```

The approved patch is written to `.data/artifacts/<task_id>/<run_id>/final.patch` and can be applied with `git apply`.

### 5. Run the web UI (optional)

```bash
docker compose up -d postgres
export SWE_DATABASE_URL=postgresql://swe:swe@localhost:5432/swe_agent
swe-agent db upgrade

swe-agent --env-file configs/models/ollama-small.env api       # http://127.0.0.1:8000/docs
swe-agent --env-file configs/models/ollama-small.env worker
streamlit run frontend/app.py                                  # http://localhost:8501
```

### 6. Run an evaluation (optional)

```bash
swe-agent --env-file configs/models/ollama-small.env experiment run configs/experiments/smoke.toml
swe-agent experiment report <experiment_id>
```

### Model configuration

| Profile | Models | Intended hardware |
|---|---|---|
| `configs/models/ollama-small.env` | `qwen2.5-coder:7b` for all stages | 6–8 GB GPU |
| `configs/models/ollama-dev.env` | `qwen3:8b` for reasoning, `qwen2.5-coder:7b` for coding | GPU with memory for both models |
| `configs/models/vllm-gpu.env` | `Qwen3-Coder-30B-A3B-Instruct` via vLLM | Dedicated GPU server |

Any other model can be used by setting `MODEL_PROVIDER`, `MODEL_BASE_URL`, `MODEL_REASONING` and `MODEL_CODER`.

---

## Project structure

```
src/swe_agent/
  graph/          LangGraph state machine and nodes
  llm/            model providers and gateway
  tools/          typed tools and policy-checked executor
  sandbox/        Docker sandbox and image builder
  verification/   patch validation, failure classification, verification levels
  api/            FastAPI application
  worker/         job queue worker
  db/             schema, migrations, data access
  baselines/      ReAct and single-shot baseline systems
  evaluation/     experiment runner, metrics, reports
frontend/         Streamlit web UI
benchmarks/       benchmark repositories and tasks
configs/          model and experiment configurations
docs/             design and setup documentation
tests/            unit, integration, security, sandbox and backend tests
```

---

## Documentation

- [Architecture](docs/architecture.md): system design, decisions and trade-offs
- [Backend](docs/backend.md): API endpoints, job lifecycle, crash recovery, observability
- [Evaluation](docs/evaluation.md): benchmark design, metric definitions, hypotheses
- [Windows setup](docs/windows-setup.md): installation on WSL2 with Docker and Ollama
- [Architecture decision records](docs/adr/)

---

## License

Released under the [MIT License](LICENSE).
