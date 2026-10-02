"""In-memory ledger: entries grouped into accounts, with date and month filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from .money import total
from .parser import Entry, parse_ledger

_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


def month_bounds(month: str) -> Tuple[date, date]:
    """Return the first and last day of a ``YYYY-MM`` month (both inclusive).

    >>> month_bounds("2024-02")
    (datetime.date(2024, 2, 1), datetime.date(2024, 2, 29))
    """
    match = _MONTH_PATTERN.match(month)
    if not match:
        raise ValueError(f"month must look like YYYY-MM, got {month!r}")
    year, mon = int(match.group(1)), int(match.group(2))
    if not 1 <= mon <= 12:
        raise ValueError(f"month out of range: {month!r}")
    first = date(year, mon, 1)
    if mon == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, mon + 1, 1)
    return first, following - timedelta(days=1)


@dataclass
class Account:
    """A named account and the entries posted to it."""

    name: str
    entries: List[Entry] = field(default_factory=list)

    @property
    def balance(self) -> Decimal:
        return total(entry.amount for entry in self.entries)


class Ledger:
    """An ordered collection of entries (sorted by date, stable for equal dates)."""

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self.entries: List[Entry] = sorted(entries, key=lambda entry: entry.date)

    @classmethod
    def from_text(cls, text: str) -> "Ledger":
        return cls(parse_ledger(text))

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def accounts(self) -> Dict[str, Account]:
        """Accounts keyed by name, in alphabetical order."""
        grouped: Dict[str, Account] = {}
        for entry in self.entries:
            grouped.setdefault(entry.account, Account(entry.account)).entries.append(entry)
        return dict(sorted(grouped.items()))

    def balance(self, account: Optional[str] = None) -> Decimal:
        """Balance of one account, or of the whole ledger when ``account`` is None."""
        return total(e.amount for e in self.entries if account is None or e.account == account)

    def between(self, start: Optional[date] = None, end: Optional[date] = None) -> "Ledger":
        """Entries dated from ``start`` to ``end`` inclusive (either bound may be open)."""
        return Ledger(
            e
            for e in self.entries
            if (start is None or e.date >= start) and (end is None or e.date <= end)
        )

    def for_month(self, month: str) -> "Ledger":
        first, last = month_bounds(month)
        return self.between(first, last)

    def for_account(self, account: str) -> "Ledger":
        return Ledger(e for e in self.entries if e.account == account.lower())

    def months(self) -> List[str]:
        """Distinct ``YYYY-MM`` months that have entries, in chronological order."""
        return sorted({entry.month for entry in self.entries})
