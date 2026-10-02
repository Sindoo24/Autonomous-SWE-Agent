"""Aggregations over a ledger and plain-text table rendering."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from .accounts import Ledger
from .money import ZERO, average, quantize
from .parser import Entry


@dataclass(frozen=True)
class MonthSummary:
    """Income and spending for one month. ``expenses`` is a positive magnitude."""

    month: str
    income: Decimal
    expenses: Decimal
    net: Decimal


def monthly_summary(ledger: Ledger) -> List[MonthSummary]:
    """One :class:`MonthSummary` per month with entries, oldest first."""
    income: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    expenses: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    for entry in ledger:
        if entry.is_expense:
            expenses[entry.month] += -entry.amount
        else:
            income[entry.month] += entry.amount
    rows = []
    for month in ledger.months():
        month_income = quantize(income[month])
        month_expenses = quantize(expenses[month])
        rows.append(MonthSummary(month, month_income, month_expenses, month_income - month_expenses))
    return rows


def category_totals(entries: Sequence[Entry], expenses_only: bool = True) -> List[tuple]:
    """Total per category as ``(category, amount)`` pairs, largest first.

    With ``expenses_only`` (the default) income is ignored and spending is reported as
    positive amounts. Ties are ordered by category name.
    """
    totals: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    for entry in entries:
        if expenses_only:
            if entry.is_expense:
                totals[entry.category] += -entry.amount
        else:
            totals[entry.category] += entry.amount
    return sorted(
        ((category, quantize(amount)) for category, amount in totals.items()),
        key=lambda pair: (-pair[1], pair[0]),
    )


def average_monthly_spending(ledger: Ledger) -> Decimal:
    """Mean of the monthly expense totals over the months present in the ledger."""
    return average(row.expenses for row in monthly_summary(ledger))


def render_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], align: Optional[str] = None
) -> str:
    """Render rows as an aligned text table.

    ``align`` has one character per column, ``"l"`` or ``"r"``; by default the first column
    is left-aligned and the others right-aligned. Columns are separated by two spaces and
    the header is underlined with dashes.
    """
    columns = len(headers)
    align = align or "l" + "r" * (columns - 1)
    if len(align) != columns:
        raise ValueError("align must have one character per column")
    widths = [len(h) for h in headers]
    for row in rows:
        if len(row) != columns:
            raise ValueError(f"row has {len(row)} cells, expected {columns}")
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]

    def fmt(cells: Sequence[str]) -> str:
        parts = [
            cell.ljust(width) if align[i] == "l" else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(cells, widths))
        ]
        return "  ".join(parts).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)
