"""The Event record and log-level vocabulary shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

#: Canonical levels and their severity rank (higher is more severe).
LEVELS: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

LEVEL_ALIASES: dict[str, str] = {
    "WARN": "WARNING",
    "ERR": "ERROR",
    "FATAL": "CRITICAL",
    "TRACE": "DEBUG",
}


class UnknownLevelError(ValueError):
    pass


def normalize_level(name: str) -> str:
    """Map ``warn``/``Warning``/``WARN`` etc. to the canonical upper-case level name."""
    upper = name.strip().upper()
    upper = LEVEL_ALIASES.get(upper, upper)
    if upper not in LEVELS:
        raise UnknownLevelError(f"unknown log level: {name!r}")
    return upper


def level_rank(name: str) -> int:
    return LEVELS[normalize_level(name)]


@dataclass(frozen=True)
class Event:
    """One log event. ``timestamp`` is always timezone-aware and in UTC."""

    timestamp: datetime
    level: str
    source: str
    message: str
    duration_ms: Optional[float] = None
    user: Optional[str] = None
    fields: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def severity(self) -> int:
        return LEVELS[self.level]
