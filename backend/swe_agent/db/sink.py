"""Persist trajectory events to PostgreSQL without blocking the run.

The recorder calls the sink synchronously (from the event loop and from sandbox threads); the sink
only enqueues. A background thread batches inserts. `close()` flushes everything that was
recorded. Inserts are idempotent (`ON CONFLICT DO NOTHING` on (run_id, seq)), so replaying a
trajectory after a crash is harmless.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from swe_agent.db.models import Event
from swe_agent.observability.logging import get_logger
from swe_agent.observability.trajectory import TrajectoryEvent

_log = get_logger("db.sink")
_STOP = object()
BATCH = 200


class DbEventSink:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._q: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="db-event-sink", daemon=True)
        self._thread.start()
        self.written = 0
        self.failed = 0

    def __call__(self, ev: TrajectoryEvent) -> None:
        self._q.put(ev)

    def close(self, timeout: float = 30.0) -> None:
        self._q.put(_STOP)
        self._thread.join(timeout)

    def _loop(self) -> None:
        stop = False
        while not stop:
            batch: list[TrajectoryEvent] = []
            item = self._q.get()
            if item is _STOP:
                break
            batch.append(item)
            while len(batch) < BATCH:
                try:
                    item = self._q.get(timeout=0.05)
                except queue.Empty:
                    break
                if item is _STOP:
                    stop = True
                    break
                batch.append(item)
            self._write(batch)

    def _write(self, batch: list[TrajectoryEvent]) -> None:
        rows = [
            {
                "run_id": ev.run_id,
                "seq": ev.seq,
                "ts": datetime.fromisoformat(ev.ts),
                "event": ev.event,
                "node": ev.node,
                "iteration": ev.iteration,
                "data": _strip_nul(json.loads(ev.model_dump_json(include={"data"}))["data"]),
            }
            for ev in batch
        ]
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(Event).on_conflict_do_nothing(), rows)
            self.written += len(rows)
        except Exception as exc:
            self.failed += len(rows)
            _log.warning("event_insert_failed", error=str(exc)[:500], events=len(rows))


def _strip_nul(value: Any) -> Any:
    """Postgres JSONB rejects NUL characters, which tool output can contain."""
    if isinstance(value, str):
        return value.replace("\x00", "\\u0000")
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value
