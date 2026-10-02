from algokit.scheduling import max_non_overlapping, overlaps


def test_long_meeting_does_not_block_short_ones():
    assert max_non_overlapping([(1, 10), (2, 3), (4, 5)]) == [(2, 3), (4, 5)]


def test_docstring_example():
    assert max_non_overlapping([(1, 4), (2, 3), (3, 5)]) == [(2, 3), (3, 5)]


def test_result_is_maximal_and_disjoint():
    meetings = [(0, 6), (1, 2), (3, 4), (5, 7), (8, 9), (5, 9), (0, 1)]
    chosen = max_non_overlapping(meetings)
    assert len(chosen) == 5
    assert all(not overlaps(a, b) for i, a in enumerate(chosen) for b in chosen[i + 1 :])
    assert chosen == sorted(chosen)
