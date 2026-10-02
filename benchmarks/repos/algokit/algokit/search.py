"""Binary-search helpers over sorted sequences.

All functions accept an optional ``key`` callable which is applied to the elements of the
sequence (but not to the target), mirroring :func:`bisect.bisect_left` in Python 3.10+.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

KeyFunc = Optional[Callable[[Any], Any]]


def _identity(value: Any) -> Any:
    return value


def lower_bound(
    seq: Sequence[Any],
    target: Any,
    lo: int = 0,
    hi: Optional[int] = None,
    key: KeyFunc = None,
) -> int:
    """Return the first index ``i`` in ``[lo, hi)`` such that ``key(seq[i]) >= target``.

    If every element is smaller than ``target`` the result is ``hi``. Equivalent to
    :func:`bisect.bisect_left`.

    >>> lower_bound([1, 2, 2, 3], 2)
    1
    """
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(seq)
    key = key or _identity
    while lo < hi:
        mid = (lo + hi) // 2
        if key(seq[mid]) < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def upper_bound(
    seq: Sequence[Any],
    target: Any,
    lo: int = 0,
    hi: Optional[int] = None,
    key: KeyFunc = None,
) -> int:
    """Return the first index ``i`` in ``[lo, hi)`` such that ``key(seq[i]) > target``.

    Equivalent to :func:`bisect.bisect_right`.

    >>> upper_bound([1, 2, 2, 3], 2)
    3
    """
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(seq)
    key = key or _identity
    while lo < hi:
        mid = (lo + hi) // 2
        if target < key(seq[mid]):
            hi = mid
        else:
            lo = mid + 1
    return lo


def binary_search(seq: Sequence[Any], target: Any, key: KeyFunc = None) -> int:
    """Return the index of an element equal to ``target``, or ``-1`` if absent.

    When several elements compare equal, the index of any one of them may be returned.
    """
    key = key or _identity
    lo, hi = 0, len(seq) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        value = key(seq[mid])
        if value == target:
            return mid
        if value < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def count_in_range(seq: Sequence[Any], low: Any, high: Any) -> int:
    """Count elements ``x`` of the sorted ``seq`` with ``low <= x <= high``."""
    if high < low:
        return 0
    return upper_bound(seq, high) - lower_bound(seq, low)


def first_true(lo: int, hi: int, predicate: Callable[[int], bool]) -> int:
    """Return the smallest integer ``n`` in ``[lo, hi)`` for which ``predicate(n)`` holds.

    ``predicate`` must be monotonic (all ``False`` followed by all ``True``). Returns ``hi``
    when the predicate is never true.

    >>> first_true(0, 100, lambda n: n * n >= 50)
    8
    """
    while lo < hi:
        mid = (lo + hi) // 2
        if predicate(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def isqrt(n: int) -> int:
    """Integer square root computed with :func:`first_true` (floor of the real root)."""
    if n < 0:
        raise ValueError("isqrt of a negative number")
    return first_true(0, n + 2, lambda k: k * k > n) - 1
