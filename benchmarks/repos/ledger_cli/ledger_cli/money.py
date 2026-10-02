"""Monetary amounts: parsing, rounding and formatting.

All amounts are :class:`decimal.Decimal` values rounded to whole cents. Rounding is
"half up" (away from zero on a tie), which is what bank statements and receipts use.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, List, Union

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}

AMOUNT_PATTERN = re.compile(r"^[+-]?[$€£₹]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$")

Number = Union[Decimal, int, str]


def quantize(amount: Number) -> Decimal:
    """Round ``amount`` to cents using half-up rounding.

    >>> quantize(Decimal("2.345"))
    Decimal('2.35')
    """
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(text: str) -> Decimal:
    """Parse a ledger amount such as ``-42.50``, ``+3``, ``1,250.00`` or ``-$9.99``.

    Raises :class:`ValueError` for anything that is not a plain decimal amount.
    """
    raw = text.strip()
    if not raw or not AMOUNT_PATTERN.match(raw) or not any(ch.isdigit() for ch in raw):
        raise ValueError(f"not an amount: {text!r}")
    sign = -1 if raw.startswith("-") else 1
    digits = raw.lstrip("+-").lstrip("".join(CURRENCY_SYMBOLS.values())).replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the pattern above
        raise ValueError(f"not an amount: {text!r}") from exc
    return quantize(sign * value)


def format_money(amount: Number, currency: str = "USD") -> str:
    """Format an amount with a currency symbol and thousands separators.

    >>> format_money(Decimal("-1234.5"))
    '-$1,234.50'
    >>> format_money(Decimal("12"), "CHF")
    '12.00 CHF'
    """
    value = quantize(amount)
    sign = "-" if value < 0 else ""
    body = f"{abs(value):,.2f}"
    symbol = CURRENCY_SYMBOLS.get(currency.upper())
    if symbol is None:
        return f"{sign}{body} {currency.upper()}"
    return f"{sign}{symbol}{body}"


def total(amounts: Iterable[Decimal]) -> Decimal:
    """Sum amounts, returning ``0.00`` for an empty iterable."""
    return quantize(sum(amounts, ZERO))


def average(amounts: Iterable[Decimal]) -> Decimal:
    """Arithmetic mean rounded to cents; ``0.00`` when there are no amounts."""
    values = list(amounts)
    if not values:
        return ZERO
    return quantize(sum(values, ZERO) / len(values))


def split_evenly(amount: Number, parts: int) -> List[Decimal]:
    """Split ``amount`` into ``parts`` shares that differ by at most one cent.

    The shares always add up to exactly the (rounded) amount; leftover cents go to the
    first shares.

    >>> split_evenly(Decimal("10.00"), 3)
    [Decimal('3.34'), Decimal('3.33'), Decimal('3.33')]
    """
    if parts <= 0:
        raise ValueError("parts must be positive")
    cents = int(quantize(amount) / CENT)
    base, remainder = divmod(abs(cents), parts)
    sign = -1 if cents < 0 else 1
    shares = [base + (1 if i < remainder else 0) for i in range(parts)]
    return [quantize(Decimal(sign * share) * CENT) for share in shares]
