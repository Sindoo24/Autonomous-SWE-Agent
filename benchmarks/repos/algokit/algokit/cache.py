"""A least-recently-used cache and a memoisation decorator built on it."""

from __future__ import annotations

import functools
from collections import OrderedDict
from typing import Any, Callable, Hashable, Iterator, Optional

_MISSING = object()


class LRUCache:
    """Mapping with a fixed capacity that evicts the least recently used entry.

    Both :meth:`get` (on a hit) and :meth:`put` mark a key as most recently used.
    ``hits`` and ``misses`` count lookups made through :meth:`get`.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self._data: "OrderedDict[Hashable, Any]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return the cached value for ``key`` (refreshing its recency) or ``default``."""
        if key not in self._data:
            self.misses += 1
            return default
        self.hits += 1
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: Hashable, value: Any) -> None:
        """Insert or update ``key``, evicting the least recently used entries if needed."""
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def peek(self, key: Hashable, default: Any = None) -> Any:
        """Return the value for ``key`` without affecting recency or statistics."""
        return self._data.get(key, default)

    def pop(self, key: Hashable, default: Any = _MISSING) -> Any:
        if default is _MISSING:
            return self._data.pop(key)
        return self._data.pop(key, default)

    def keys(self) -> list:
        """Keys from least to most recently used."""
        return list(self._data)

    def clear(self) -> None:
        self._data.clear()
        self.hits = self.misses = 0

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[Hashable]:
        return iter(self._data)

    def __repr__(self) -> str:
        return f"LRUCache(capacity={self.capacity}, size={len(self)})"


def lru_memoize(capacity: int = 128) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator caching results of a function with hashable positional arguments."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        cache = LRUCache(capacity)

        @functools.wraps(func)
        def wrapper(*args: Hashable) -> Any:
            result: Optional[Any] = cache.get(args, _MISSING)
            if result is _MISSING:
                result = func(*args)
                cache.put(args, result)
            return result

        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator
