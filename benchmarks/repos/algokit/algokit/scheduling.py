"""Interval scheduling: selecting, merging and packing half-open time intervals.

Intervals are ``(start, end)`` tuples with ``start <= end`` describing the half-open range
``[start, end)``, so a meeting ending at 10 does not conflict with one starting at 10.
"""

from __future__ import annotations

import heapq
from typing import List, Sequence, Tuple

Interval = Tuple[float, float]


def _validate(intervals: Sequence[Interval]) -> None:
    for start, end in intervals:
        if start > end:
            raise ValueError(f"interval ends before it starts: {(start, end)}")


def overlaps(a: Interval, b: Interval) -> bool:
    """Return True if two half-open intervals share any point."""
    return a[0] < b[1] and b[0] < a[1]


def max_non_overlapping(intervals: Sequence[Interval]) -> List[Interval]:
    """Select the largest set of pairwise non-overlapping intervals.

    Uses the classic earliest-finish-time greedy strategy. The selected intervals are
    returned in chronological order.

    >>> max_non_overlapping([(1, 4), (2, 3), (3, 5)])
    [(2, 3), (3, 5)]
    """
    _validate(intervals)
    chosen: List[Interval] = []
    last_end = float("-inf")
    for start, end in sorted(intervals, key=lambda iv: (iv[1], iv[0])):
        if start >= last_end:
            chosen.append((start, end))
            last_end = end
    return chosen


def min_rooms(intervals: Sequence[Interval]) -> int:
    """Minimum number of rooms needed to host every meeting."""
    _validate(intervals)
    ends: List[float] = []
    rooms = 0
    for start, end in sorted(intervals):
        while ends and ends[0] <= start:
            heapq.heappop(ends)
        heapq.heappush(ends, end)
        rooms = max(rooms, len(ends))
    return rooms


def merge(intervals: Sequence[Interval]) -> List[Interval]:
    """Merge overlapping or adjacent intervals into a sorted, disjoint list."""
    _validate(intervals)
    merged: List[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def free_slots(busy: Sequence[Interval], window: Interval, min_length: float = 0) -> List[Interval]:
    """Return the gaps inside ``window`` not covered by any ``busy`` interval.

    Gaps shorter than ``min_length`` are omitted.
    """
    day_start, day_end = window
    slots: List[Interval] = []
    cursor = day_start
    for start, end in merge(busy):
        if end <= day_start or start >= day_end:
            continue
        if start > cursor and start - cursor >= min_length:
            slots.append((cursor, min(start, day_end)))
        cursor = max(cursor, end)
    if day_end > cursor and day_end - cursor >= min_length:
        slots.append((cursor, day_end))
    return slots
