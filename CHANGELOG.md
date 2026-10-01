# Changelog

## 2026-09-28: fixes for three test failures on a developer machine

- `runner.run_task`: U+0000 is removed from the issue before it enters the checkpointed state.
  PostgreSQL cannot store it; `intake` still rejects an issue that is empty after sanitising.
- `sandbox/image.py`: `PIP_NO_INPUT` / `PIP_DISABLE_PIP_VERSION_CHECK` are set only for the
  dependency-install build step, not in the runtime environment of the sandbox.
- `cli.py`: approve, reject and status now say which checkpoint store was searched when a run is
  not found (PostgreSQL vs SQLite).
- `tests/conftest.py`: the `settings` fixture ignores `SWE_DATABASE_URL` / `SWE_API_KEY` exported in
  the developer's shell, so test results no longer depend on it.

## Phases 5–8 (2026-09-28): backend, observability, evaluation harness, UI

Tested with a scripted model, PostgreSQL 16 and the Docker sandbox only; no real-model results.
See docs/phase-5-8-report.md.

- **Phase 5.**
  - PostgreSQL schema + Alembic (`swe-agent db upgrade`).
  - FastAPI (`swe-agent api`, 23 endpoints, API key, restricted repo sources, idempotency keys, whitelisted per-run overrides).
  - Postgres job queue + worker (`swe-agent worker`) with heartbeats, crash recovery from checkpoints, max attempts.
  - LangGraph Postgres checkpoints; events persisted; cooperative cancel (`cancelled` termination).
  - Workspaces are now per run.
- **Phase 6.**
  - OpenTelemetry spans from trajectory events (`spans.jsonl`, OTLP/Jaeger optional).
  - SQL views for tool/model/sandbox calls, node durations, latency breakdown, tokens and tool usage; `/runs/{id}/metrics`.
- **Phase 7.**
  - 20 new benchmark tasks (25 total, all validated) in four new repositories.
  - Baselines A (single-shot) and B (ReAct, with a sandboxed `run_tests` tool).
  - Agent ablation flags `failure_routing=generic` and `candidate_seeding=false`.
  - Experiment runner (`swe-agent experiment run|evaluate|report|list`) with held-out and first-attempt evaluation.
  - SQL metric views; report generator (markdown, CSV, chart, cost estimate with stated assumptions).
  - Pre-registration in docs/evaluation.md.
  - Held-out totals now count parametrised cases.
- **Phase 8.** Streamlit UI (`frontend/`): submit, live progress, plan, tool calls, iterations, tests, diff, approve/reject, cancel, experiments.
- Tests: 238 passed, 1 skipped (live); coverage 90%.

## Phases 3 and 4 (2026-09-28) — recovery, L4/L5 verification, human approval

Implemented on request before real-model Phase 2/3 results were available; every behaviour below
is exercised with a scripted model and the real Docker sandbox only. See docs/phase-3-4-report.md.

Phase 3 (recovery)
- New `reproduce` node (plan → reproduce → implement): the model writes a reproduction test as
  structured output; it is kept only if it fails on the unpatched code for the right reason.
  Up to `AGENT_REPRODUCE_ATTEMPTS` (3) with pytest feedback; non-fatal. Skipped when a
  baseline-failing test already covers the issue. The accepted test joins `baseline.failing`,
  so L2/L3 use it like any other test; it is read-only for `implement` and guarded in `run_tests`.
- `analyze_failure`: model-written `RootCauseAnalysis` for test failures/regressions (advisory,
  rule-based fallback on model error; `fix_in_right_place=false` re-routes to explore).
- Stagnation: the same failure signature a second time escalates one level
  (implement → plan → explore → stop); a third time stops (`repeated_failure`).
- Oscillation: an accepted patch identical to an earlier one stops the run (`oscillation`).
- Rollback: explore-level retries reset the workspace to the base commit (keeping the
  reproduction test), rebuild the symbol index and clear the tool executor's failure memory.

Phase 4 (verification + approval)
- L4: `ruff check --select E9,F` on changed files; only findings not present at the base commit
  block L4. `ruff` is now installed in the sandbox dependency image (one-time image rebuild).
- L5: `--acceptance DIR` / `AGENT_ACCEPTANCE_DIR` — tests copied only into the sandbox snapshot,
  never into the agent's workspace.
- `human_approval` node (verify → human_approval → finalize): `interrupt()` + SQLite checkpoints
  (`SWE_CHECKPOINT_DB`); `swe-agent approve|reject [--feedback --retry]|status RUN_ID` resume from
  any process. `approval_request.json` + `pending.patch` for review. `run --auto-approve`;
  `bench run` and the live test always auto-approve. New termination `rejected_by_human`.
- `finalize` checks `final.patch` applies to the base commit (`patch_applies_cleanly`).
- report.json: `reproduction`, `root_cause_analyses`, `rollbacks`, `approval`,
  `patch_applies_cleanly`, `verification_before_termination`.
- Tests: 212 passed, 1 skipped (live); coverage 92%.

## Phase 2.1 (2026-09-28) — tool-layer recovery fix from the first real-model run

Observed with `qwen2.5-coder:7b` (live smoke test): `implement` called
`edit_file(search="def create_user(request):")` — text that exists nowhere in the repo — and
repeated the identical call until the budget ran out.

- `tools/edit_tools.py`: when nothing in the file resembles `search`, the error now includes the
  file's current text (files ≤ 60 lines / 2,500 chars, unnumbered so it can be copied verbatim)
  or an outline with line ranges (larger files), plus the rule "copy `search` verbatim from the
  file; new code goes only in `replace`". Matching semantics unchanged.
- `tools/executor.py`: a call identical to one that already failed (no successful write since)
  is flagged `REPEATED FAILED CALL … take a different action`; trajectory records
  `repeated_failure`. Error-message cap 1,500 → 3,500 chars.
- `tests/live/test_live_model.py`: stale Phase 1 assertions replaced (termination reasons now
  derived from `TerminationReason`).
- Tests: +6 unit, +1 integration replaying the live trace. 186 passed, 1 skipped (live).

Not Phase 3: no reproduce step, LLM failure analysis, or rollback/escalation yet.

## Phase 2 (2026-09-27)

Docker sandbox, baseline/target test runs, failure classifier, verification levels L1–L3,
5-task dev benchmark. See docs/phase-2-report.md.
