import pytest

from algokit.cache import LRUCache, lru_memoize


def test_put_evicts_oldest():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert "a" not in cache
    assert cache.keys() == ["b", "c"]


def test_get_refreshes_recency():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.keys() == ["a", "c"]


def test_stats_and_peek():
    cache = LRUCache(3)
    cache.put("x", 10)
    assert cache.get("x") == 10
    assert cache.get("y", "none") == "none"
    assert cache.peek("x") == 10
    assert (cache.hits, cache.misses) == (1, 1)


def test_negative_capacity():
    with pytest.raises(ValueError):
        LRUCache(-1)


def test_memoize():
    calls = []

    @lru_memoize(capacity=4)
    def square(n):
        calls.append(n)
        return n * n

    assert [square(3), square(3), square(4)] == [9, 9, 16]
    assert calls == [3, 4]
