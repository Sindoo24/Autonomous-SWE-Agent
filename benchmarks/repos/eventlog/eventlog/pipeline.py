"""Compose parse -> filter -> aggregate into a single report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Optional

from eventlog import aggregate, filters, sessions
from eventlog.events import Event
from eventlog.parse import parse_lines


@dataclass
class Pipeline:
    """A reusable chain of filters with a fixed report shape.

    >>> Pipeline().where(filters.min_level("error")).run(lines)  # doctest: +SKIP
    """

    predicates: list[filters.Predicate] = field(default_factory=list)
    session_gap: timedelta = sessions.DEFAULT_GAP
    default_tz: tzinfo = timezone.utc

    def where(self, predicate: filters.Predicate) -> "Pipeline":
        self.predicates.append(predicate)
        return self

    def select(self, lines: Iterable[str], errors: Optional[list[tuple[int, str]]] = None) -> list[Event]:
        events = parse_lines(lines, default_tz=self.default_tz, errors=errors)
        events = filters.apply(events, *self.predicates)
        return sorted(events, key=lambda e: e.timestamp)

    def run(self, lines: Iterable[str]) -> dict[str, Any]:
        errors: list[tuple[int, str]] = []
        events = self.select(lines, errors)
        return {
            "events": len(events),
            "skipped_lines": [lineno for lineno, _ in errors],
            "first": events[0].timestamp if events else None,
            "last": events[-1].timestamp if events else None,
            "levels": aggregate.count_by_level(events),
            "per_minute": aggregate.per_minute(events),
            "durations": aggregate.duration_stats(events),
            "top_sources": aggregate.top_sources(events),
            "error_rate": aggregate.error_rate(events),
            "sessions": sessions.session_summary(sessions.sessionize(events, self.session_gap)),
        }


def run_pipeline(
    lines: Iterable[str],
    *,
    min_level: Optional[str] = None,
    sources: Optional[Iterable[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    session_gap: timedelta = sessions.DEFAULT_GAP,
) -> dict[str, Any]:
    """Convenience wrapper building a :class:`Pipeline` from keyword options."""
    pipeline = Pipeline(session_gap=session_gap)
    if min_level:
        pipeline.where(filters.min_level(min_level))
    if sources:
        pipeline.where(filters.from_sources(sources))
    if start is not None or end is not None:
        pipeline.where(filters.between(start, end))
    return pipeline.run(lines)
