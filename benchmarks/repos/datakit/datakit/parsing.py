"""Parsing helpers for values exported from spreadsheets and bank statements."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class ParseError(ValueError):
    pass


def parse_amount(text: str) -> Decimal:
    """Parse a money amount such as "1,234.50", "-12.00", "(15.25)" or "$ 3.10".

    Parentheses denote a negative amount (accounting notation). Thousands separators and a
    leading currency symbol are ignored.
    """
    raw = text.strip()
    if not raw:
        raise ParseError("empty amount")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    raw = raw.lstrip("$€£").strip()
    raw = raw.replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ParseError(f"not an amount: {text!r}") from exc
    return -value if negative else value


def parse_bool(text: str) -> bool:
    value = text.strip().lower()
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n", ""}:
        return False
    raise ParseError(f"not a boolean: {text!r}")
