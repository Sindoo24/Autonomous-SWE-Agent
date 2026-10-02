from algokit.search import count_in_range
from algokit.sequences import lis_length, longest_increasing_subsequence


def test_lis_with_repeated_values_is_strict():
    assert longest_increasing_subsequence([1, 2, 2, 3]) == [1, 2, 3]
    result = longest_increasing_subsequence([4, 4, 1, 5, 5, 2, 6, 6])
    assert len(result) == 3
    assert all(a < b for a, b in zip(result, result[1:]))


def test_lis_length_of_constant_sequence():
    assert lis_length([5, 5, 5, 5]) == 1


def test_count_in_range_with_duplicates():
    assert count_in_range([1, 2, 2, 2, 3], 2, 2) == 3
    assert count_in_range([1, 1, 4, 4, 9], 1, 4) == 4
