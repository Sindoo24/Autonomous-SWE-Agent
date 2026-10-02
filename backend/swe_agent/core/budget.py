"""Run-level budget meter shared by the LLM gateway, the tool executor and the graph."""

from __future__ import annotations

import threading
import time

from swe_agent.core.errors import BudgetExceeded
from swe_agent.schemas.state import Budget


class BudgetMeter:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget.model_copy()
        self._t0 = time.monotonic() - budget.elapsed_seconds
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ accounting

    def add_tokens(self, n: int | None) -> None:
        if n is None:
            return
        with self._lock:
            self.budget.used_tokens += n

    def add_tool_call(self) -> None:
        with self._lock:
            self.budget.used_tool_calls += 1

    def add_iteration(self) -> None:
        with self._lock:
            self.budget.used_iterations += 1

    # ------------------------------------------------------------------ checks

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def check(self) -> None:
        b = self.budget
        if b.used_tokens >= b.max_tokens:
            raise BudgetExceeded("tokens", b.used_tokens, b.max_tokens)
        if b.used_tool_calls >= b.max_tool_calls:
            raise BudgetExceeded("tool_calls", b.used_tool_calls, b.max_tool_calls)
        if self.elapsed >= b.max_wall_seconds:
            raise BudgetExceeded("wall_seconds", round(self.elapsed, 1), b.max_wall_seconds)

    def snapshot(self) -> Budget:
        with self._lock:
            snap = self.budget.model_copy()
        snap.elapsed_seconds = round(self.elapsed, 3)
        return snap
