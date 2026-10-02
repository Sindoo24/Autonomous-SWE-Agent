"""Parsing of plain-text ledger files.

Each non-empty line records one transaction::

    2024-01-05 groceries at market -42.50 #food @checking

* the first token is an ISO date,
* ``#tag`` tokens attach tags (the first tag is the entry's category),
* an ``@account`` token names the account (default: ``checking``),
* the last remaining token is the signed amount, and everything before it is the
  free-text description.

Lines starting with ``;`` are comments, and ``;`` also starts a trailing comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Tuple

from .money import parse_amount

DEFAULT_ACCOUNT = "checking"
UNCATEGORIZED = "uncategorized"

_TAG_PATTERN = re.compile(r"^[\w/-]+$")


class ParseError(ValueError):
    """A ledger line could not be parsed. ``line_no`` is 1-based (0 if unknown)."""

    def __init__(self, message: str, line_no: int = 0) -> None:
        super().__init__(f"line {line_no}: {message}" if line_no else message)
        self.line_no = line_no


@dataclass(frozen=True)
class Entry:
    """A single ledger transaction. Negative amounts are money going out."""

    date: date
    description: str
    amount: Decimal
    tags: Tuple[str, ...] = ()
    account: str = DEFAULT_ACCOUNT

    @property
    def category(self) -> str:
        return self.tags[0] if self.tags else UNCATEGORIZED

    @property
    def is_expense(self) -> bool:
        return self.amount < 0

    @property
    def month(self) -> str:
        return f"{self.date.year:04d}-{self.date.month:02d}"


def _parse_tag(token: str) -> str:
    """Turn ``#Food`` into ``food``; tags are case-insensitive."""
    tag = token[1:].strip().lower()
    if not tag or not _TAG_PATTERN.match(tag):
        raise ValueError(f"invalid tag {token!r}")
    return tag


def parse_line(line: str, line_no: int = 0) -> Entry:
    """Parse one transaction line into an :class:`Entry`."""
    content = line.split(";", 1)[0].strip()
    tokens = content.split()
    if len(tokens) < 2:
        raise ParseError("expected '<date> [description] <amount> [#tags] [@account]'", line_no)
    try:
        when = date.fromisoformat(tokens[0])
    except ValueError:
        raise ParseError(f"invalid date {tokens[0]!r}", line_no) from None

    tags: List[str] = []
    account = DEFAULT_ACCOUNT
    body: List[str] = []
    for token in tokens[1:]:
        if token.startswith("#"):
            try:
                tag = _parse_tag(token)
            except ValueError as exc:
                raise ParseError(str(exc), line_no) from None
            if tag not in tags:
                tags.append(tag)
        elif token.startswith("@") and len(token) > 1:
            account = token[1:].lower()
        else:
            body.append(token)

    if not body:
        raise ParseError("missing amount", line_no)
    try:
        amount = parse_amount(body[-1])
    except ValueError:
        raise ParseError(f"invalid amount {body[-1]!r}", line_no) from None
    description = " ".join(body[:-1]) or "(no description)"
    return Entry(when, description, amount, tuple(tags), account)


def parse_ledger(text: str) -> List[Entry]:
    """Parse a whole ledger file, skipping blank lines and comment lines."""
    entries = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        entries.append(parse_line(stripped, line_no))
    return entries
