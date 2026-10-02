"""Runtime configuration.

All configuration comes from environment variables (optionally loaded from an env file),
so switching Ollama -> vLLM -> any OpenAI-compatible endpoint is a config change only.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderKind(StrEnum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class ToolMode(StrEnum):
    """How the model expresses tool calls.

    native_tools: provider-native function calling (quality varies by model/server).
    json_action:  the model emits a JSON object constrained by a schema; works with any
                  server that supports JSON-schema constrained decoding.
    """

    NATIVE_TOOLS = "native_tools"
    JSON_ACTION = "json_action"


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODEL_", extra="ignore")

    provider: ProviderKind = ProviderKind.OLLAMA
    base_url: str = "http://localhost:11434"
    api_key: str | None = None
    # role -> model name. "reasoning" drives explore/hypothesize/plan; "coder" drives implement.
    reasoning: str = "qwen3:8b"
    coder: str = "qwen2.5-coder:7b"
    tool_mode: ToolMode = ToolMode.JSON_ACTION
    temperature: float = 0.0
    seed: int | None = 7
    max_output_tokens: int = 2048
    num_ctx: int = 16384  # Ollama context window; ignored by OpenAI-compatible servers
    timeout_s: float = 300.0
    max_retries: int = 2
    # Qwen3-style models: disable "thinking" output to save tokens (false). For models without a
    # thinking mode (e.g. qwen2.5-coder) set MODEL_THINK=none so the field is not sent at all:
    # some Ollama versions reject `think` for such models.
    think: bool | None = False

    @field_validator("think", mode="before")
    @classmethod
    def _think_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().lower() in {"", "none", "null", "unset", "default"}:
            return None
        return v


class BudgetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDGET_", extra="ignore")

    max_iterations: int = 5
    max_tool_calls: int = 80
    max_tokens: int = 400_000
    max_wall_seconds: int = 1800
    explore_max_steps: int = 25
    implement_max_steps: int = 20
    max_validation_attempts: int = 3


class SandboxSettings(BaseSettings):
    """Docker sandbox. Everything that executes repository or generated code runs here."""

    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")

    # False: patches are generated and statically validated, never executed.
    enabled: bool = True
    docker_bin: str = "docker"
    # Any image with python3 (+ venv module) on PATH. The sandbox base image is built on top.
    base_image: str = "python:3.12-slim"
    python_version: str = "3.12"
    # Highest manylinux glibc level the base image supports (python:3.12-slim = Debian 12 = 2.36).
    manylinux_max: str = "2_36"
    user: str = "10001:10001"
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 256
    tmpfs_size: str = "256m"
    nofile: int = 1024
    runtime: str | None = None  # e.g. "runsc" for gVisor
    test_timeout_s: int = 300
    import_timeout_s: int = 60
    build_timeout_s: int = 900
    max_output_bytes: int = 1_000_000
    # Always installed into the dependency image in addition to the repo's own requirements.
    # ruff powers the L4 lint check (new violations on changed files only).
    extra_requirements: list[str] = Field(default_factory=lambda: ["pytest", "ruff"])


class AgentSettings(BaseSettings):
    """Agent behaviour switches."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    # Write a failing test that reproduces the bug before fixing it (skipped when an
    # existing baseline-failing test already covers the issue).
    reproduce: bool = True
    reproduce_attempts: int = 3
    # LLM root-cause analysis for test failures / regressions (rules decide the rest).
    llm_failure_analysis: bool = True
    # "manual" pauses before finalize until `swe-agent approve|reject`; "auto" approves
    # (benchmarks, tests). Nothing leaves the isolated workspace either way.
    approval: Literal["manual", "auto"] = "manual"
    # L4 lint check and optional L5 acceptance tests (a directory of test_*.py files the
    # agent never sees).
    lint: bool = True
    acceptance_dir: Path | None = None
    # Ablation switches for the evaluation (defaults = the full system).
    # "generic": no failure classification, escalation or root-cause analysis; every failed test
    # run goes back to implement with the raw pytest output, until the iteration budget ends.
    failure_routing: Literal["classified", "generic"] = "classified"
    # False: explore gets no deterministic candidate-file ranking.
    candidate_seeding: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWE_", extra="ignore")

    workspace_root: Path = Path("./.data/workspaces")
    artifacts_root: Path = Path("./.data/artifacts")
    log_level: str = "INFO"
    log_json: bool = True
    # Maximum characters of tool output shown to the model per call.
    tool_output_max_chars: int = 12_000
    # Soft cap on tool-loop conversation size before old tool outputs are elided.
    loop_context_max_chars: int = 60_000
    max_patch_changed_lines: int = 200
    # Benchmark dataset (repos/ + tasks/). Relative paths resolve against the working directory.
    benchmarks_dir: Path = Path("benchmarks")

    model: ModelSettings = Field(default_factory=ModelSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    # Durable graph checkpoints (resume after a restart / after human approval). Used when no
    # database_url is set (CLI mode); with Postgres the checkpoints live in the database.
    checkpoint_db: Path = Path("./.data/checkpoints.sqlite")

    # PostgreSQL for the API / worker, e.g.
    # postgresql+psycopg://swe:swe@localhost:5432/swe_agent. Unset = CLI-only mode (SQLite
    # checkpoints, files only); the API and worker require it.
    database_url: str | None = None
    # Static API key (header X-API-Key). Unset = no auth: bind the API to localhost only.
    api_key: str | None = None
    # Local repository paths the API accepts, comma-separated (https git URLs are always
    # accepted). Anything outside these roots is refused, so the API cannot be used to read
    # arbitrary directories.
    api_repo_roots: str = "."
    worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: float = 10.0
    # A running job whose worker has not sent a heartbeat for this long is requeued (the run
    # continues from its last checkpoint).
    worker_stale_seconds: float = 120.0
    job_max_attempts: int = 3

    # OpenTelemetry. "jsonl" writes spans.jsonl next to the trajectory; "otlp" exports
    # to SWE_OTEL_ENDPOINT (needs opentelemetry-exporter-otlp); "none" disables spans.
    otel_exporter: Literal["none", "jsonl", "otlp", "console"] = "jsonl"
    otel_endpoint: str = "http://localhost:4318/v1/traces"

    def repo_roots(self) -> list[Path]:
        return [Path(p.strip()).resolve() for p in self.api_repo_roots.split(",") if p.strip()]


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings, optionally from a profile env file (e.g. configs/models/ollama-dev.env)."""
    if env_file is None:
        return Settings()
    kwargs = {"_env_file": str(env_file)}
    return Settings(
        model=ModelSettings(**kwargs),  # type: ignore[arg-type]
        budget=BudgetSettings(**kwargs),  # type: ignore[arg-type]
        sandbox=SandboxSettings(**kwargs),  # type: ignore[arg-type]
        agent=AgentSettings(**kwargs),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# Settings a run may override through the API / experiment configs. Anything else (model
# endpoint, API keys, sandbox security flags, paths) is deployment configuration only.
OVERRIDABLE: dict[str, frozenset[str]] = {
    "agent": frozenset(
        {
            "reproduce",
            "reproduce_attempts",
            "llm_failure_analysis",
            "approval",
            "lint",
            "failure_routing",
            "candidate_seeding",
        }
    ),
    "budget": frozenset(BudgetSettings.model_fields),
    "model": frozenset({"seed", "temperature"}),
}


class OverrideError(ValueError):
    pass


def check_overrides(overrides: dict[str, dict[str, object]] | None) -> None:
    for section, values in (overrides or {}).items():
        allowed = OVERRIDABLE.get(section)
        if allowed is None or not isinstance(values, dict):
            raise OverrideError(f"unknown settings section {section!r}")
        bad = sorted(set(values) - allowed)
        if bad:
            raise OverrideError(f"{section}: not overridable per run: {bad}")


def apply_overrides(settings: Settings, overrides: dict[str, dict[str, object]] | None) -> Settings:
    """A validated copy of `settings` with per-run overrides applied."""
    check_overrides(overrides)
    out = settings.model_copy(deep=True)
    for section, values in (overrides or {}).items():
        current = getattr(out, section)
        merged = current.model_dump() | values
        setattr(out, section, type(current).model_validate(merged))
    return out
