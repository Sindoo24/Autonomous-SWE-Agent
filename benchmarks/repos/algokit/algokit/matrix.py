"""Dense matrix helpers on lists of lists (row-major)."""

from __future__ import annotations

from typing import List, Sequence

Matrix = List[List[float]]


def shape(matrix: Sequence[Sequence[float]]) -> tuple:
    """Return ``(rows, cols)``; raises ``ValueError`` for ragged input."""
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if any(len(row) != cols for row in matrix):
        raise ValueError("matrix rows have different lengths")
    return rows, cols


def identity(n: int) -> Matrix:
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def transpose(matrix: Sequence[Sequence[float]]) -> Matrix:
    rows, cols = shape(matrix)
    return [[matrix[r][c] for r in range(rows)] for c in range(cols)]


def matmul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix:
    """Multiply an ``n x m`` matrix by an ``m x p`` matrix."""
    n, m = shape(a)
    m2, p = shape(b)
    if m != m2:
        raise ValueError(f"cannot multiply {n}x{m} by {m2}x{p}")
    return [[sum(a[i][k] * b[k][j] for k in range(m)) for j in range(p)] for i in range(n)]


def rotate_clockwise(matrix: Sequence[Sequence[float]]) -> Matrix:
    """Rotate a matrix 90 degrees clockwise."""
    return [list(reversed(column)) for column in transpose(matrix)]


def spiral_order(matrix: Sequence[Sequence[float]]) -> list:
    """Return the elements in clockwise spiral order starting at the top-left corner."""
    rows, cols = shape(matrix)
    result = []
    top, bottom, left, right = 0, rows - 1, 0, cols - 1
    while top <= bottom and left <= right:
        result.extend(matrix[top][c] for c in range(left, right + 1))
        result.extend(matrix[r][right] for r in range(top + 1, bottom + 1))
        if top < bottom:
            result.extend(matrix[bottom][c] for c in range(right - 1, left - 1, -1))
        if left < right:
            result.extend(matrix[r][left] for r in range(bottom - 1, top, -1))
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    return result

