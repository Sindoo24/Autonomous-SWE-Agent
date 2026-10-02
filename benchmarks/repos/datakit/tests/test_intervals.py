import pytest

from datakit.intervals import merge_intervals, total_length


def test_merge_overlapping():
    assert merge_intervals([(1, 3), (2, 5), (7, 8)]) == [(1, 5), (7, 8)]


def test_merge_unsorted_input():
    assert merge_intervals([(7, 8), (1, 3)]) == [(1, 3), (7, 8)]


def test_invalid_interval():
    with pytest.raises(ValueError):
        merge_intervals([(5, 1)])


def test_total_length():
    assert total_length([(0, 2), (1, 3)]) == 3
