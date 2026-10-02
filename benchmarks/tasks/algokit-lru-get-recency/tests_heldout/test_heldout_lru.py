from algokit.cache import LRUCache, lru_memoize


def test_read_key_survives_eviction():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert "a" in cache
    assert "b" not in cache


def test_recency_order_after_reads():
    cache = LRUCache(3)
    for key in "xyz":
        cache.put(key, key.upper())
    cache.get("x")
    cache.get("y")
    assert cache.keys() == ["z", "x", "y"]
    assert (cache.hits, cache.misses) == (2, 0)


def test_memoized_hot_value_not_recomputed():
    calls = []

    @lru_memoize(capacity=2)
    def double(n):
        calls.append(n)
        return 2 * n

    double(1)
    double(2)
    double(1)
    double(3)
    double(1)
    assert calls == [1, 2, 3]
