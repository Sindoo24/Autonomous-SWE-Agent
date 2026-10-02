"""Command-line entry point: ``python -m ledger_cli FILE COMMAND [options]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .accounts import Ledger
from .commands import COMMANDS
from .parser import ParseError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Plain-text bookkeeping reports.")
    parser.add_argument("file", help="ledger file to read")
    parser.add_argument("--currency", default="USD", help="currency code used for display")
    sub = parser.add_subparsers(dest="command", required=True)

    def scoped(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--month", help="only entries in this YYYY-MM month")
        cmd.add_argument("--account", help="only entries posted to this account")
        return cmd

    scoped("balance", "balance per account and overall")
    scoped("register", "every entry with a running balance")
    scoped("monthly", "income, expenses and net per month")
    categories = scoped("categories", "spending per category, largest first")
    categories.add_argument("--top", type=int, help="show only the N largest categories")
    categories.add_argument("--all", action="store_true", help="include income (signed totals)")
    scoped("summary", "headline numbers")
    split = scoped("split", "split spending evenly between people")
    split.add_argument("--people", type=int, default=2)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the CLI and return a process exit code (0 ok, 1 bad ledger, 2 usage error)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ledger: cannot read {path}: {exc.strerror}", file=sys.stderr)
        return 2
    try:
        ledger = Ledger.from_text(text)
        output = COMMANDS[args.command](ledger, args)
    except ParseError as exc:
        print(f"ledger: {path}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0
