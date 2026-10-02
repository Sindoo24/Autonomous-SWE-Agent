"""Composable event filters. Each takes and returns a list, preserving order."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from eventlog.events import Event, level_rank

Predicate = Callable[[Event], bool]


def min_level(level: str) -> Predicate:
    """Keep events at ``level`` or more severe (``min_level("warn")`` keeps ERROR too)."""
    threshold = level_rank(level)
    return lambda event: event.severity >= threshold


def from_sources(sources: Iterable[str]) -> Predicate:
    """Keep events whose source is one of ``sources`` or a dotted child of one."""
    wanted = tuple(s.strip() for s in sources if s.strip())
    return lambda event: any(
        event.source == s or event.source.startswith(s + ".") for s in wanted
    )


def between(start: Optional[datetime] = None, end: Optional[datetime] = None) -> Predicate:
    """Keep events with ``start <= timestamp < end``; either bound may be omitted."""

    def keep(event: Event) -> bool:
        if start is not None and event.timestamp < start:
            return False
        return end is None or event.timestamp < end

    return keep


def message_matches(pattern: str, *, ignore_case: bool = True) -> Predicate:
    regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    return lambda event: regex.search(event.message) is not None


def has_fields(**expected: Any) -> Predicate:
    """Keep events whose ``fields`` contain every given key with the given value."""
    return lambda event: all(event.fields.get(k) == v for k, v in expected.items())


def apply(events: Iterable[Event], *predicates: Predicate) -> list[Event]:
    return [e for e in events if all(p(e) for p in predicates)]
