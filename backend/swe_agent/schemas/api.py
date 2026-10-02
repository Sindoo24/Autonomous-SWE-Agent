"""Request / response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from swe_agent.db.models import AgentRun

System = Literal["agent", "baseline_single_shot", "baseline_react"]


class TaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(description="https git URL, or a local directory under SWE_API_REPO_ROOTS")
    issue: str = Field(min_length=10, max_length=20_000)
    budget: dict[str, Any] | None = None
    created_by: str | None = Field(default=None, max_length=200)


class TaskCreated(BaseModel):
    task_id: str
    repository: str


class RunCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: System = "agent"
    variant: str = Field(default="full", max_length=32, pattern=r"^[a-z0-9_]+$")
    # "manual" pauses before finalize for POST /runs/{id}/approve|reject
    approval: Literal["manual", "auto"] = "manual"
    # validated against config.OVERRIDABLE (agent flags, budget, model seed / temperature)
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def effective_overrides(self) -> dict[str, dict[str, Any]]:
        out = {k: dict(v) for k, v in self.overrides.items()}
        out.setdefault("agent", {})["approval"] = self.approval
        return out


class RunCreated(BaseModel):
    run_id: str
    created: bool
    status: str


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decided_by: str | None = Field(default=None, max_length=200)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str | None = Field(default=None, max_length=4000)
    retry: bool = False
    decided_by: str | None = Field(default=None, max_length=200)


def run_out(r: AgentRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "system": r.system,
        "variant": r.variant,
        "status": r.status,
        "termination_reason": r.termination_reason,
        "verification_level": r.verification_level,
        "verification_status": r.verification_status,
        "iterations": r.iterations,
        "tool_calls": r.total_tool_calls,
        "model_calls": r.total_model_calls,
        "tokens_in": r.total_tokens_in,
        "tokens_out": r.total_tokens_out,
        "wall_ms": r.wall_ms,
        "seed": r.seed,
        "experiment_id": r.experiment_id,
        "repeat": r.repeat,
        "cancel_requested": r.cancel_requested,
        "error": r.error,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }
