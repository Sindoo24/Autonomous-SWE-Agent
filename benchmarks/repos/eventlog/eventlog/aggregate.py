"""Aggregations over event lists: level counts, time buckets and duration statistics."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Iterable, Optional, Sequence

from eventlog.events import LEVELS, Event


def count_by_level(events: Iterable[Event]) -> dict[str, int]:
    """Counts per level, ordered from least to most severe, omitting absent levels."""
    counts = Counter(e.level for e in events)
    return {level: counts[level] for level in LEVELS if counts[level]}


def minute_of(ts: datetime) -> datetime:
    """The start of the minute containing ``ts``."""
    return ts.replace(second=0, microsecond=0)


def per_minute(events: Iterable[Event]) -> dict[datetime, int]:
    """Number of events in each calendar minute, in chronological order.

    Minutes with no events are not included.
    """
    counts = Counter(minute_of(e.timestamp) for e in events)
    return dict(sorted(counts.items()))


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (same definition as numpy's default).

    Returns ``None`` for an empty sequence.
    """
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def duration_stats(events: Iterable[Event]) -> dict[str, Optional[float]]:
    """min / mean / p50 / p95 / p99 / max over events that carry a duration."""
    durations = [e.duration_ms for e in events if e.duration_ms is not None]
    return {
        "count": len(durations),
        "min": min(durations, default=None),
        "mean": _mean(durations),
        "p50": percentile(durations, 50),
        "p95": percentile(durations, 95),
        "p99": percentile(durations, 99),
        "max": max(durations, default=None),
    }


def top_sources(events: Iterable[Event], n: int = 5) -> list[tuple[str, int]]:
    """The ``n`` busiest sources; ties are broken alphabetically."""
    counts = Counter(e.source for e in events)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def error_rate(events: Sequence[Event]) -> float:
    """Fraction of events at ERROR or above (0.0 for no events)."""
    if not events:
        return 0.0
    return sum(1 for e in events if e.severity >= LEVELS["ERROR"]) / len(events)
