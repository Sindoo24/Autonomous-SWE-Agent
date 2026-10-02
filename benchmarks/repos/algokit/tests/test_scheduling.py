import pytest

from algokit.scheduling import free_slots, max_non_overlapping, merge, min_rooms, overlaps


def test_overlaps_is_half_open():
    assert overlaps((1, 3), (2, 4))
    assert not overlaps((1, 2), (2, 3))


def test_max_non_overlapping_back_to_back():
    assert max_non_overlapping([(1, 2), (2, 3), (3, 4)]) == [(1, 2), (2, 3), (3, 4)]


def test_min_rooms():
    assert min_rooms([(0, 30), (5, 10), (15, 20)]) == 2
    assert min_rooms([(1, 2), (2, 3)]) == 1
    assert min_rooms([]) == 0


def test_merge():
    assert merge([(5, 7), (1, 3), (2, 4)]) == [(1, 4), (5, 7)]


def test_free_slots():
    busy = [(9, 10), (12, 13), (12.5, 14)]
    assert free_slots(busy, (8, 17)) == [(8, 9), (10, 12), (14, 17)]
    assert free_slots(busy, (8, 17), min_length=2) == [(10, 12), (14, 17)]


def test_invalid_interval():
    with pytest.raises(ValueError):
        min_rooms([(3, 1)])
