import pytest

from algokit.matrix import identity, matmul, rotate_clockwise, spiral_order, transpose


def test_transpose_and_identity():
    assert transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]
    assert matmul([[1, 2], [3, 4]], identity(2)) == [[1, 2], [3, 4]]


def test_matmul_shape_mismatch():
    with pytest.raises(ValueError):
        matmul([[1, 2]], [[1, 2]])


def test_rotate():
    assert rotate_clockwise([[1, 2], [3, 4]]) == [[3, 1], [4, 2]]


def test_spiral_order():
    assert spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]
    assert spiral_order([[1, 2, 3, 4], [5, 6, 7, 8]]) == [1, 2, 3, 4, 8, 7, 6, 5]
    assert spiral_order([[1], [2], [3]]) == [1, 2, 3]

