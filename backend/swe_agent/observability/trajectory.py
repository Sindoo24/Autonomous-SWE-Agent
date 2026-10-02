"""Trajectory recorder.

Every node transition, tool call and model call becomes one structured event, appended to
`trajectory.jsonl` in the run's artifact directory and kept in memory for run summaries.
Sinks receive every event too: PostgreSQL (`db.sink`) and OpenTelemetry spans
(`observability.otel`). The event shape is the same everywhere.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from swe_agent.observability.logging import get_logger

EventType = Literal[
    "run_started",
    "run_finished",
    "run_continued",  # a worker continued the run from its last checkpoint
    "node_started",
    "node_finished",
    "tool_call",
    "model_call",
    "injection_flag",
    "sandbox_exec",
    "recovery",  # reproduce outcome, escalation, rollback, root-cause analysis
    "approval",  # approval requested / decided
]

_log = get_logger("trajectory")


class TrajectoryEvent(BaseModel):
    seq: int
    ts: str
    task_id: str
    run_id: str
    event: EventType
    node: str | None = None
    iteration: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


EventSink = Callable[["TrajectoryEvent"], None]


class TrajectoryRecorder:
    def __init__(
        self, task_id: str, run_id: str, path: Path, sinks: Sequence[EventSink] = ()
    ) -> None:
        self.task_id = task_id
        self.sinks = list(sinks)
        self.run_id = run_id
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[TrajectoryEvent] = []
        self._seq = 0
        if self.path.exists():  # resuming a run: keep sequence numbers monotonic, keep history
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = TrajectoryEvent.model_validate_json(line)
                except ValueError:
                    continue
                self.events.append(ev)
                self._seq = max(self._seq, ev.seq)
        self._lock = threading.Lock()
        self.current_node: str | None = None
        self.current_iteration: int | None = None

    def record(self, event: EventType, **data: Any) -> TrajectoryEvent:
        with self._lock:
            self._seq += 1
            ev = TrajectoryEvent(
                seq=self._seq,
                ts=datetime.now(UTC).isoformat(),
                task_id=self.task_id,
                run_id=self.run_id,
                event=event,
                node=self.current_node,
                iteration=self.current_iteration,
                data=data,
            )
            self.events.append(ev)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(ev.model_dump_json() + "\n")
            for sink in self.sinks:
                try:
                    sink(ev)
                except Exception as exc:
                    _log.warning("sink_error", sink=repr(sink), error=str(exc)[:300])
        _log.info(
            event,
            task_id=self.task_id,
            run_id=self.run_id,
            node=ev.node,
            iteration=ev.iteration,
            **{k: v for k, v in data.items() if k in _LOG_KEYS},
        )
        return ev

    # ------------------------------------------------------------------ summaries

    def summary(self) -> dict[str, Any]:
        # System steps (`finish`, the patch validator) are traced but are not model tool calls.
        sandbox = [e for e in self.events if e.event == "sandbox_exec"]
        tool_calls = [e for e in self.events if e.event == "tool_call" and not e.data.get("system")]
        model_calls = [e for e in self.events if e.event == "model_call"]
        tokens_in: list[int | None] = [e.data.get("tokens_in") for e in model_calls]
        tokens_out: list[int | None] = [e.data.get("tokens_out") for e in model_calls]
        by_tool: dict[str, int] = {}
        for e in tool_calls:
            name = str(e.data.get("tool"))
            by_tool[name] = by_tool.get(name, 0) + 1
        return {
            "tool_calls": len(tool_calls),
            "tool_calls_by_tool": by_tool,
            "tool_errors": sum(1 for e in tool_calls if e.data.get("status") != "ok"),
            "model_calls": len(model_calls),
            "tokens_in": _sum_or_none(tokens_in),
            "tokens_out": _sum_or_none(tokens_out),
            "model_latency_ms": sum(int(e.data.get("latency_ms", 0)) for e in model_calls),
            "tool_latency_ms": sum(int(e.data.get("duration_ms", 0)) for e in tool_calls),
            "injection_flags": sum(1 for e in self.events if e.event == "injection_flag"),
            "test_runs": sum(1 for e in sandbox if e.data.get("op") != "build_image"),
            "sandbox_ms": sum(int(e.data.get("duration_ms", 0)) for e in sandbox),
        }


def _sum_or_none(values: list[int | None]) -> int | None:
    """None if any provider response omitted usage: token counts are never estimated."""
    if any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


_LOG_KEYS = {
    "tool",
    "status",
    "duration_ms",
    "latency_ms",
    "role",
    "model",
    "tokens_in",
    "tokens_out",
    "error",
    "termination_reason",
    "next",
}


class Stopwatch:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)
