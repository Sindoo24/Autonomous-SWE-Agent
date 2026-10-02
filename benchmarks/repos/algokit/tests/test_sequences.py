import pytest

from algokit.sequences import (
    dedupe,
    lis_length,
    longest_increasing_subsequence,
    max_window_sum,
    sliding_max,
)


def test_lis_example():
    assert longest_increasing_subsequence([10, 9, 2, 5, 3, 7, 101, 18]) == [2, 3, 7, 18]


def test_lis_empty_and_sorted():
    assert longest_increasing_subsequence([]) == []
    assert lis_length([1, 2, 3, 4]) == 4
    assert lis_length([9, 7, 5]) == 1


def test_max_window_sum():
    assert max_window_sum([1, -2, 3, 4, -1], 2) == 7
    assert max_window_sum([5, 1, 1, 5, 5], 3) == 11


def test_max_window_sum_rejects_bad_sizes():
    with pytest.raises(ValueError):
        max_window_sum([1, 2], 0)
    with pytest.raises(ValueError):
        max_window_sum([1, 2], 3)


def test_sliding_max():
    assert sliding_max([1, 3, -1, -3, 5, 3, 6, 7], 3) == [3, 3, 5, 5, 6, 7]


def test_dedupe_preserves_order():
    assert dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
    assert dedupe(["Ab", "ab", "B"], key=str.lower) == ["Ab", "B"]

