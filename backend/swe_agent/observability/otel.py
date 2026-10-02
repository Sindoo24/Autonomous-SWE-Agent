"""OpenTelemetry spans derived from trajectory events.

One trace per run execution (a resumed or continued run gets a new trace with the same
`swe.run_id`): a root span `run`, a span per node, and child spans per model call, tool call and
sandbox execution. Spans are built from the recorder's events, which are the single choke point
every node, tool and model call already goes through, so instrumentation cannot drift from the
trajectory. Child spans are back-dated from their recorded durations.

Exporters: `jsonl` (default; spans.jsonl next to trajectory.jsonl), `otlp` (HTTP, e.g. Jaeger
with the compose `jaeger` profile; needs `pip install opentelemetry-exporter-otlp-proto-http`),
`console`, `none`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Status, StatusCode

from swe_agent.config import Settings
from swe_agent.observability.logging import get_logger
from swe_agent.observability.trajectory import TrajectoryEvent

_log = get_logger("otel")
CHILD_EVENTS = {"tool_call", "model_call", "sandbox_exec"}
SPAN_EVENTS = {"recovery", "approval", "injection_flag", "run_continued"}
ATTR_KEYS = (
    "tool", "status", "error_type", "role", "purpose", "provider", "model", "tokens_in",
    "tokens_out", "latency_ms", "duration_ms", "op", "test_run", "exit_code", "passed", "failed",
    "errors", "timed_out", "repeated", "truncated", "action", "termination_reason",
)  # fmt: skip


def _ns(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp() * 1e9)


def _attrs(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ATTR_KEYS:
        v = data.get(k)
        if isinstance(v, bool | int | float | str):
            out[f"swe.{k}"] = v
    return out


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        lines = []
        for sp in spans:
            ctx = sp.get_span_context()
            parent = sp.parent
            start, end = sp.start_time or 0, sp.end_time or 0
            lines.append(
                json.dumps(
                    {
                        "trace_id": format(ctx.trace_id, "032x") if ctx else None,
                        "span_id": format(ctx.span_id, "016x") if ctx else None,
                        "parent_id": format(parent.span_id, "016x") if parent else None,
                        "name": sp.name,
                        "start_ns": start,
                        "end_ns": end,
                        "duration_ms": round((end - start) / 1e6, 3),
                        "status": sp.status.status_code.name,
                        "attributes": dict(sp.attributes or {}),
                        "events": [e.name for e in sp.events],
                    }
                )
            )
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _exporter(settings: Settings, path: Path) -> tuple[SpanExporter, bool] | None:
    kind = settings.otel_exporter
    if kind == "jsonl":
        return JsonlSpanExporter(path), False
    if kind == "console":
        return ConsoleSpanExporter(), False
    if kind == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )
        except ImportError:
            _log.warning("otlp_exporter_missing", hint="pip install opentelemetry-exporter-otlp")
            return None
        return OTLPSpanExporter(endpoint=settings.otel_endpoint), True
    return None


class SpanSink:
    """Recorder sink that turns events into spans. Not thread-safe on its own: the recorder
    calls sinks while holding its lock."""

    def __init__(self, provider: TracerProvider, task_id: str, run_id: str) -> None:
        self.provider = provider
        self.tracer = provider.get_tracer("swe_agent")
        self.task_id = task_id
        self.run_id = run_id
        self.root: Span | None = None
        self.node: Span | None = None

    def __call__(self, ev: TrajectoryEvent) -> None:
        t = _ns(ev.ts)
        if self.root is None:
            self.root = self.tracer.start_span(
                "run",
                start_time=t,
                attributes={"swe.task_id": self.task_id, "swe.run_id": self.run_id},
            )
        if ev.event == "node_started":
            self._end_node(t)
            ctx = trace.set_span_in_context(self.root)
            self.node = self.tracer.start_span(
                f"node:{ev.node}",
                context=ctx,
                start_time=t,
                attributes={"swe.node": ev.node or "", "swe.iteration": ev.iteration or 0},
            )
        elif ev.event == "node_finished":
            if self.node is not None:
                self.node.set_attributes(_attrs(ev.data))
                if ev.data.get("status") not in (None, "ok"):
                    self.node.set_status(Status(StatusCode.ERROR, str(ev.data.get("status"))))
            self._end_node(t)
        elif ev.event in CHILD_EVENTS:
            parent = self.node or self.root
            dur_ms = ev.data.get("duration_ms", ev.data.get("latency_ms", 0)) or 0
            start = t - int(float(dur_ms) * 1e6)
            name = {
                "tool_call": f"tool:{ev.data.get('tool')}",
                "model_call": f"model:{ev.data.get('role')}",
                "sandbox_exec": f"sandbox:{ev.data.get('op')}",
            }[ev.event]
            span = self.tracer.start_span(
                name, context=trace.set_span_in_context(parent), start_time=start,
                attributes=_attrs(ev.data),
            )  # fmt: skip
            if ev.data.get("status") not in (None, "ok", "passed"):
                span.set_status(Status(StatusCode.ERROR, str(ev.data.get("status"))))
            span.end(end_time=max(t, start))
        elif ev.event in SPAN_EVENTS:
            (self.node or self.root).add_event(ev.event, attributes=_attrs(ev.data), timestamp=t)
        elif ev.event == "run_finished":
            self.root.set_attributes(_attrs(ev.data))
            self._end_node(t)
            self.root.end(end_time=t)
            self.root = None
            self.provider.force_flush()

    def _end_node(self, t: int) -> None:
        if self.node is not None:
            self.node.end(end_time=t)
            self.node = None

    def close(self) -> None:
        """End spans left open (the run crashed or paused mid-node) and flush."""
        if self.root is not None:
            now = int(datetime.now().timestamp() * 1e9)
            self._end_node(now)
            self.root.set_attribute("swe.incomplete", True)
            self.root.end(end_time=now)
            self.root = None
        self.provider.shutdown()


def span_sink_for(settings: Settings, task_id: str, run_id: str) -> SpanSink | None:
    art = settings.artifacts_root.resolve() / task_id / run_id
    made = _exporter(settings, art / "spans.jsonl")
    if made is None:
        return None
    exporter, batch = made
    art.mkdir(parents=True, exist_ok=True)
    provider = TracerProvider(
        resource=Resource.create({"service.name": "swe-agent", "swe.run_id": run_id})
    )
    provider.add_span_processor(
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    )
    return SpanSink(provider, task_id, run_id)
