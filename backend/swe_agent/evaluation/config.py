"""Experiment configuration: which systems / ablations run on which tasks, how often.

    name = "main"
    tasks = "all"            # or ["users-missing-email", ...]
    repeats = 3
    seeds = [7, 8, 9]        # model seed per repeat (optional; default: the configured seed)

    [[systems]]
    system = "agent"         # agent | baseline_single_shot | baseline_react
    variant = "full"         # agent ablations: see VARIANTS

Every run gets the same model, sandbox, budget and held-out evaluation; only the system /
variant differs (plus the per-repeat seed).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from swe_agent.config import check_overrides

# Ablations of the agent (README: Evaluation).
VARIANTS: dict[str, dict[str, dict[str, Any]]] = {
    "full": {},
    "no_reproduce": {"agent": {"reproduce": False}},
    "generic_retry": {"agent": {"failure_routing": "generic", "llm_failure_analysis": False}},
    "no_candidate_seeding": {"agent": {"candidate_seeding": False}},
    "single_iteration": {"budget": {"max_iterations": 1}},
}


class SystemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: Literal["agent", "baseline_single_shot", "baseline_react"]
    variant: str = Field(default="full", pattern=r"^[a-z0-9_]+$")
    # extra overrides on top of the variant's (validated like API overrides)
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> SystemSpec:
        if self.system == "agent" and self.variant not in VARIANTS and not self.overrides:
            raise ValueError(
                f"unknown agent variant {self.variant!r}; known: {sorted(VARIANTS)} "
                "(or give explicit overrides)"
            )
        if self.system != "agent" and self.variant != "full":
            raise ValueError("variants apply to the agent only")
        check_overrides(self.effective_overrides())
        return self

    @property
    def label(self) -> str:
        return self.system if self.variant == "full" else f"{self.system}:{self.variant}"

    def effective_overrides(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        base = VARIANTS.get(self.variant, {}) if self.system == "agent" else {}
        for section in {*base, *self.overrides}:
            out[section] = {**base.get(section, {}), **self.overrides.get(section, {})}
        return out


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    tasks: list[str] | Literal["all"] = "all"
    repeats: int = Field(default=1, ge=1, le=20)
    seeds: list[int] | None = None
    systems: list[SystemSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _seeds(self) -> ExperimentConfig:
        if self.seeds is not None and len(self.seeds) != self.repeats:
            raise ValueError(f"seeds must have one entry per repeat ({self.repeats})")
        labels = [s.label for s in self.systems]
        if len(labels) != len(set(labels)):
            raise ValueError(f"duplicate systems: {labels}")
        return self

    def seed_for(self, repeat: int, default: int | None) -> int | None:
        return self.seeds[repeat] if self.seeds else default


def load_experiment(path: str | Path) -> ExperimentConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return ExperimentConfig.model_validate(data)
