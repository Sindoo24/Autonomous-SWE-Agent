"""Implementation of the ``ledger`` sub-commands. Each returns the text to print."""

from __future__ import annotations

import argparse

from .accounts import Ledger
from .money import ZERO, format_money, split_evenly
from .report import average_monthly_spending, category_totals, monthly_summary, render_table


def _scope(ledger: Ledger, args: argparse.Namespace) -> Ledger:
    """Apply the common ``--month`` / ``--account`` filters."""
    if getattr(args, "month", None):
        ledger = ledger.for_month(args.month)
    if getattr(args, "account", None):
        ledger = ledger.for_account(args.account)
    return ledger


def cmd_balance(ledger: Ledger, args: argparse.Namespace) -> str:
    ledger = _scope(ledger, args)
    rows = [
        [name, format_money(account.balance, args.currency)]
        for name, account in ledger.accounts().items()
    ]
    rows.append(["total", format_money(ledger.balance(), args.currency)])
    return render_table(["account", "balance"], rows)


def cmd_register(ledger: Ledger, args: argparse.Namespace) -> str:
    ledger = _scope(ledger, args)
    rows = []
    running = ZERO
    for entry in ledger:
        running += entry.amount
        rows.append(
            [
                entry.date.isoformat(),
                entry.description,
                entry.category,
                format_money(entry.amount, args.currency),
                format_money(running, args.currency),
            ]
        )
    return render_table(
        ["date", "description", "category", "amount", "balance"], rows, align="lllrr"
    )


def cmd_monthly(ledger: Ledger, args: argparse.Namespace) -> str:
    ledger = _scope(ledger, args)
    rows = [
        [
            row.month,
            format_money(row.income, args.currency),
            format_money(row.expenses, args.currency),
            format_money(row.net, args.currency),
        ]
        for row in monthly_summary(ledger)
    ]
    return render_table(["month", "income", "expenses", "net"], rows)


def cmd_categories(ledger: Ledger, args: argparse.Namespace) -> str:
    ledger = _scope(ledger, args)
    totals = category_totals(ledger.entries, expenses_only=not args.all)
    if args.top is not None:
        totals = totals[: args.top]
    rows = [[category, format_money(amount, args.currency)] for category, amount in totals]
    return render_table(["category", "total"], rows)


def cmd_summary(ledger: Ledger, args: argparse.Namespace) -> str:
    ledger = _scope(ledger, args)
    months = ledger.months()
    lines = [
        f"entries: {len(ledger)}",
        f"months: {len(months)}" + (f" ({months[0]} to {months[-1]})" if months else ""),
        f"balance: {format_money(ledger.balance(), args.currency)}",
        f"average monthly spending: {format_money(average_monthly_spending(ledger), args.currency)}",
    ]
    return "\n".join(lines)


def cmd_split(ledger: Ledger, args: argparse.Namespace) -> str:
    """Split the spending of the selected scope between ``--people`` people."""
    ledger = _scope(ledger, args)
    spent = sum((-e.amount for e in ledger if e.is_expense), ZERO)
    shares = split_evenly(spent, args.people)
    lines = [f"total spending: {format_money(spent, args.currency)}"]
    lines.extend(
        f"person {i}: {format_money(share, args.currency)}" for i, share in enumerate(shares, 1)
    )
    return "\n".join(lines)


COMMANDS = {
    "balance": cmd_balance,
    "register": cmd_register,
    "monthly": cmd_monthly,
    "categories": cmd_categories,
    "summary": cmd_summary,
    "split": cmd_split,
}
