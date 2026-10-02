import bisect

from algokit.search import binary_search, count_in_range, first_true, isqrt, lower_bound, upper_bound


def test_lower_bound_missing_target():
    assert lower_bound([1, 3, 5, 7], 4) == 2
    assert lower_bound([1, 3, 5, 7], 0) == 0
    assert lower_bound([1, 3, 5, 7], 9) == 4


def test_upper_bound_matches_bisect_right():
    data = [1, 2, 2, 2, 5, 8]
    for target in range(0, 10):
        assert upper_bound(data, target) == bisect.bisect_right(data, target)


def test_binary_search():
    data = [2, 4, 6, 8, 10]
    assert binary_search(data, 8) == 3
    assert binary_search(data, 5) == -1
    assert binary_search([], 1) == -1


def test_binary_search_with_key():
    people = [("ann", 21), ("bob", 34), ("cy", 50)]
    assert binary_search(people, 34, key=lambda p: p[1]) == 1


def test_count_in_range_between_values():
    assert count_in_range([1, 4, 6, 9, 12], 3, 10) == 3
    assert count_in_range([1, 4, 6, 9, 12], 10, 3) == 0


def test_first_true_and_isqrt():
    assert first_true(0, 100, lambda n: n * n >= 50) == 8
    assert first_true(0, 10, lambda n: False) == 10
    assert [isqrt(n) for n in (0, 1, 3, 4, 15, 16, 17)] == [0, 1, 1, 2, 3, 4, 4]
