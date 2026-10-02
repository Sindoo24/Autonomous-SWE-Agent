"""Serialise events and reports as CSV or JSON."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from eventlog.events import Event

DEFAULT_COLUMNS: tuple[str, ...] = ("timestamp", "level", "source", "message", "duration_ms", "user")


def format_timestamp(ts: datetime) -> str:
    """``2024-05-01T12:00:00.250000+00:00`` style is noisy; emit ``2024-05-01T12:00:00.250Z``."""
    ts = ts.astimezone(timezone.utc)
    text = ts.strftime("%Y-%m-%dT%H:%M:%S")
    if ts.microsecond:
        text += f".{ts.microsecond // 1000:03d}"
    return text + "Z"


def _cell(event: Event, column: str) -> str:
    if column == "timestamp":
        return format_timestamp(event.timestamp)
    if column in ("level", "source", "message", "duration_ms", "user"):
        value = getattr(event, column)
    else:
        value = event.fields.get(column)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def to_csv(events: Iterable[Event], columns: Sequence[str] = DEFAULT_COLUMNS) -> str:
    """CSV with a header row; quoting follows RFC 4180 (via the csv module)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for event in events:
        writer.writerow([_cell(event, c) for c in columns])
    return buffer.getvalue()


def event_to_dict(event: Event) -> dict[str, Any]:
    record: dict[str, Any] = {
        "timestamp": format_timestamp(event.timestamp),
        "level": event.level,
        "source": event.source,
        "message": event.message,
    }
    if event.duration_ms is not None:
        record["duration_ms"] = event.duration_ms
    if event.user is not None:
        record["user"] = event.user
    record.update(event.fields)
    return record


def to_json_lines(events: Iterable[Event]) -> str:
    return "".join(json.dumps(event_to_dict(e), ensure_ascii=False) + "\n" for e in events)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return format_timestamp(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def report_to_json(report: dict[str, Any], indent: int = 2) -> str:
    """Serialise a pipeline report; datetime keys and values become ``...Z`` strings."""

    def convert(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                (format_timestamp(k) if isinstance(k, datetime) else k): convert(v)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [convert(v) for v in obj]
        return obj

    return json.dumps(convert(report), indent=indent, default=_json_default)
