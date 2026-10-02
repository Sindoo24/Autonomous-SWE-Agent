"""structlog configuration: JSON lines with bound context (task_id, run_id, node, iteration)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        # Resolve sys.stderr at logger creation (not config time) so redirected/replaced
        # streams are honoured.
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )


def _stderr_logger(*_: Any) -> structlog.PrintLogger:
    return structlog.PrintLogger(file=sys.stderr)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
