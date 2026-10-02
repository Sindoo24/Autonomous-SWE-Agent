import pytest

from algokit.sequences import max_window_sum


def test_window_equal_to_length():
    assert max_window_sum([1, 2, 3], 3) == 6
    assert max_window_sum([-4, 1, -2], 3) == -5


def test_single_element():
    assert max_window_sum([7], 1) == 7


def test_window_larger_than_sequence_still_rejected():
    with pytest.raises(ValueError):
        max_window_sum([1, 2, 3], 4)
