"""The systems a run can use: the agent (C) and the baselines (A, B).

Every system has the `run_task` signature and writes the same artifacts (report.json,
final.patch, trajectory.jsonl), so the worker, the API and the evaluation treat them alike.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Protocol

from swe_agent.agents.runner import RunOutcome, run_task
from swe_agent.config import Settings
from swe_agent.llm.base import LLMProvider
from swe_agent.observability.trajectory import EventSink


class SystemFn(Protocol):
    def __call__(
        self,
        source: str,
        issue: str,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        sinks: Sequence[EventSink] = (),
        cancel: Callable[[], bool] | None = None,
    ) -> Coroutine[Any, Any, RunOutcome]: ...


def systems() -> dict[str, SystemFn]:
    from swe_agent.evaluation.baselines.react import run_react
    from swe_agent.evaluation.baselines.single_shot import run_single_shot

    return {
        "agent": run_task,
        "baseline_single_shot": run_single_shot,
        "baseline_react": run_react,
    }


SYSTEM_NAMES = ("agent", "baseline_single_shot", "baseline_react")
# Only the agent pauses for human approval; the baselines have no approval step.
RESUMABLE = frozenset({"agent"})
