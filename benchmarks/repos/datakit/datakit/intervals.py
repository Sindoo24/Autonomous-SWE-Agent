"""Interval utilities (closed intervals of integers or floats)."""

from __future__ import annotations

Interval = tuple[float, float]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or touching closed intervals. Input order does not matter.

    >>> merge_intervals([(1, 3), (2, 5), (7, 8)])
    [(1, 5), (7, 8)]
    """
    ordered = sorted(intervals)
    merged: list[Interval] = []
    for start, end in ordered:
        if start > end:
            raise ValueError(f"invalid interval: {(start, end)}")
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def total_length(intervals: list[Interval]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))
