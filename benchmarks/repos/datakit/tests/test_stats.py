from datakit.stats import moving_average


def test_moving_average():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_window_larger_than_input():
    assert moving_average([1, 2], 3) == []
