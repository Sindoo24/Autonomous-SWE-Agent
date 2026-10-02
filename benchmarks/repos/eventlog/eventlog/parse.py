"""Turn raw log text into :class:`~eventlog.events.Event` records.

Two input formats are understood and may be mixed in one stream:

* plain text lines: ``<timestamp> <LEVEL> [<source>] <message> key=value ...``
  where trailing ``key=value`` tokens become fields (``duration`` and ``user`` are special);
* JSON lines: one object per line with ``ts``/``timestamp``, ``level``, ``source``/``logger``,
  ``msg``/``message`` and optional ``duration_ms``/``user``; other keys become fields.

Blank lines and lines starting with ``#`` are ignored.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, tzinfo
from typing import Any, Iterable, Optional

from eventlog.events import Event, UnknownLevelError, normalize_level

LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+(?P<level>[A-Za-z]+)\s+\[(?P<source>[^\]]+)\]\s*(?P<rest>.*)$"
)
FIELD_RE = re.compile(r"^(?P<key>[A-Za-z_][\w.]*)=(?P<value>\S*)$")
DURATION_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)?)(?P<unit>us|ms|s|m)?$")
DURATION_UNITS_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0, "m": 60_000.0}


class ParseError(ValueError):
    """A line could not be turned into an event."""


def parse_timestamp(text: str, default_tz: tzinfo = timezone.utc) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` suffix allowed) or epoch seconds into UTC.

    Timestamps without an offset are interpreted in ``default_tz``.
    """
    raw = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ParseError(f"bad timestamp: {text!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def parse_duration(text: str) -> float:
    """``"250ms"`` -> 250.0, ``"1.5s"`` -> 1500.0, ``"80us"`` -> 0.08; bare numbers are ms."""
    match = DURATION_RE.match(text.strip().lower())
    if not match:
        raise ParseError(f"bad duration: {text!r}")
    unit = match.group("unit") or "ms"
    return float(match.group("num")) * DURATION_UNITS_MS[unit]


def _coerce(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def _split_fields(rest: str) -> tuple[str, dict[str, Any]]:
    """Peel ``key=value`` tokens off the end of a message."""
    tokens = rest.split()
    fields: dict[str, Any] = {}
    while tokens:
        match = FIELD_RE.match(tokens[-1])
        if not match:
            break
        tokens.pop()
        fields.setdefault(match.group("key"), _coerce(match.group("value")))
    return " ".join(tokens), dict(reversed(list(fields.items())))


def _build(ts: datetime, level: str, source: str, message: str, fields: dict[str, Any],
           duration: Any = None, user: Any = None) -> Event:
    try:
        canonical = normalize_level(level)
    except UnknownLevelError as exc:
        raise ParseError(str(exc)) from None
    duration_ms: Optional[float] = None
    if duration is not None and duration != "":
        duration_ms = float(duration) if isinstance(duration, (int, float)) else parse_duration(str(duration))
    return Event(
        timestamp=ts,
        level=canonical,
        source=source.strip(),
        message=message.strip(),
        duration_ms=duration_ms,
        user=str(user) if user not in (None, "") else None,
        fields=fields,
    )


def parse_line(line: str, default_tz: tzinfo = timezone.utc) -> Event:
    match = LINE_RE.match(line.strip())
    if not match:
        raise ParseError(f"unrecognised line: {line.strip()!r}")
    message, fields = _split_fields(match.group("rest"))
    duration = fields.pop("duration", None)
    user = fields.pop("user", None)
    return _build(
        parse_timestamp(match.group("ts"), default_tz),
        match.group("level"),
        match.group("source"),
        message,
        fields,
        duration=duration,
        user=user,
    )


def parse_json_line(line: str, default_tz: tzinfo = timezone.utc) -> Event:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc.msg}") from None
    if not isinstance(record, dict):
        raise ParseError("JSON line is not an object")
    data = dict(record)
    ts = data.pop("ts", None) or data.pop("timestamp", None)
    if ts is None:
        raise ParseError("JSON line has no timestamp")
    level = data.pop("level", "INFO")
    source = data.pop("source", None) or data.pop("logger", None) or "-"
    message = data.pop("msg", None) or data.pop("message", None) or ""
    return _build(
        parse_timestamp(str(ts), default_tz),
        str(level),
        str(source),
        str(message),
        data,
        duration=data.pop("duration_ms", None),
        user=data.pop("user", None),
    )


def parse_lines(
    lines: Iterable[str],
    *,
    default_tz: tzinfo = timezone.utc,
    strict: bool = False,
    errors: Optional[list[tuple[int, str]]] = None,
) -> list[Event]:
    """Parse every line; malformed lines are skipped (and recorded in ``errors``) unless strict."""
    events: list[Event] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            if stripped.startswith("{"):
                events.append(parse_json_line(stripped, default_tz))
            else:
                events.append(parse_line(stripped, default_tz))
        except ParseError as exc:
            if strict:
                raise ParseError(f"line {lineno}: {exc}") from None
            if errors is not None:
                errors.append((lineno, str(exc)))
    return events
