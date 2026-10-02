from datakit.intervals import merge_intervals


def test_touching_intervals_merge():
    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]


def test_chain_of_touching_intervals():
    assert merge_intervals([(5, 6), (1, 2), (2, 5)]) == [(1, 6)]


def test_gap_is_preserved():
    assert merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
