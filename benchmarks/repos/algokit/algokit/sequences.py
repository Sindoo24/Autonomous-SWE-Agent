"""Algorithms over sequences: subsequences, sliding windows and order-preserving helpers."""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Hashable, Iterable, List, Optional, Sequence

from .search import lower_bound


def longest_increasing_subsequence(values: Sequence[Any]) -> List[Any]:
    """Return one longest *strictly* increasing subsequence of ``values``.

    Runs in O(n log n) using the patience-sorting technique. When several subsequences
    have the maximum length, the one ending with the smallest possible values is returned.

    >>> longest_increasing_subsequence([3, 1, 4, 1, 5, 9, 2, 6])
    [1, 4, 5, 6]
    """
    tails: List[int] = []  # tails[k] = index of the smallest tail of an increasing run of length k+1
    parents: List[Optional[int]] = [None] * len(values)
    for i, value in enumerate(values):
        pos = lower_bound(tails, value, key=lambda idx: values[idx])
        if pos > 0:
            parents[i] = tails[pos - 1]
        if pos == len(tails):
            tails.append(i)
        else:
            tails[pos] = i

    result: List[Any] = []
    cursor: Optional[int] = tails[-1] if tails else None
    while cursor is not None:
        result.append(values[cursor])
        cursor = parents[cursor]
    result.reverse()
    return result


def lis_length(values: Sequence[Any]) -> int:
    """Length of the longest strictly increasing subsequence."""
    return len(longest_increasing_subsequence(values))


def max_window_sum(values: Sequence[float], k: int) -> float:
    """Return the largest sum of ``k`` consecutive elements.

    Raises :class:`ValueError` if ``k`` is not positive or larger than ``len(values)``.

    >>> max_window_sum([1, -2, 3, 4, -1], 2)
    7
    """
    if k <= 0:
        raise ValueError("window size must be positive")
    if k > len(values):
        raise ValueError(f"window size {k} exceeds sequence length {len(values)}")
    window = sum(values[:k])
    best = window
    for i in range(k, len(values)):
        window += values[i] - values[i - k]
        best = max(best, window)
    return best


def sliding_max(values: Sequence[float], k: int) -> List[float]:
    """Return the maximum of every window of ``k`` consecutive elements (monotonic deque)."""
    if k <= 0:
        raise ValueError("window size must be positive")
    result: List[float] = []
    window: deque = deque()  # indices, values decreasing from left to right
    for i, value in enumerate(values):
        while window and values[window[-1]] <= value:
            window.pop()
        window.append(i)
        if window[0] <= i - k:
            window.popleft()
        if i >= k - 1:
            result.append(values[window[0]])
    return result


def dedupe(items: Iterable[Any], key: Optional[Callable[[Any], Hashable]] = None) -> List[Any]:
    """Remove duplicates while preserving the order of first occurrence.

    >>> dedupe(["b", "a", "b", "c", "a"])
    ['b', 'a', 'c']
    """
    seen = set()
    result = []
    for item in items:
        marker = key(item) if key else item
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result

