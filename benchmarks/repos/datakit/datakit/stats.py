"""Streaming-friendly statistics."""

from __future__ import annotations


def moving_average(values: list[float], window: int) -> list[float]:
    """Simple moving average; the output has len(values) - window + 1 items."""
    if window < 1:
        raise ValueError("window must be >= 1")
    if window > len(values):
        return []
    out = []
    acc = sum(values[:window])
    out.append(acc / window)
    for i in range(window, len(values)):
        acc += values[i] - values[i - window]
        out.append(acc / window)
    return out
