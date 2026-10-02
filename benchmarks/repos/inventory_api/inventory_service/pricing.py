"""Price quotes: volume tiers, promotion codes, tax and money rounding.

All arithmetic is done with :class:`~decimal.Decimal`. Amounts are rounded to whole cents
(half-up, like a till) at the line level; the order totals are sums of rounded lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

CENT = Decimal("0.01")
DEFAULT_TAX_RATE = Decimal("0.18")

#: (minimum quantity, percent off) -- the highest tier the quantity reaches applies.
VOLUME_TIERS: tuple[tuple[int, int], ...] = ((100, 10), (50, 5))

#: Promotion codes and the percentage they take off every line.
PROMO_CODES: dict[str, int] = {"WELCOME5": 5, "SPRING12": 12, "STAFF20": 20}


def round_money(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def volume_percent(quantity: int) -> int:
    for minimum, percent in VOLUME_TIERS:
        if quantity >= minimum:
            return percent
    return 0


def promo_percent(code: str | None) -> int:
    """Percent off for a promotion code (case-insensitive); unknown codes raise KeyError."""
    if not code:
        return 0
    return PROMO_CODES[code.strip().upper()]


def percent_of(amount: Decimal, percent: int) -> Decimal:
    return amount * Decimal(percent) / Decimal(100)


@dataclass(frozen=True)
class QuoteLine:
    sku: str
    quantity: int
    unit_price: Decimal
    percent_off: int
    subtotal: Decimal
    discount: Decimal

    @property
    def net(self) -> Decimal:
        return self.subtotal - self.discount


def price_line(sku: str, unit_price: Decimal, quantity: int, promo: int = 0) -> QuoteLine:
    """Price one order line. Volume and promotion discounts do not stack; the larger wins."""
    percent = max(volume_percent(quantity), promo)
    subtotal = round_money(unit_price * quantity)
    discount = round_money(percent_of(subtotal, percent))
    return QuoteLine(
        sku=sku,
        quantity=quantity,
        unit_price=unit_price,
        percent_off=percent,
        subtotal=subtotal,
        discount=discount,
    )


@dataclass(frozen=True)
class Quote:
    lines: tuple[QuoteLine, ...]
    tax_rate: Decimal

    @property
    def subtotal(self) -> Decimal:
        return sum((line.subtotal for line in self.lines), Decimal("0"))

    @property
    def discount(self) -> Decimal:
        return sum((line.discount for line in self.lines), Decimal("0"))

    @property
    def tax(self) -> Decimal:
        return round_money((self.subtotal - self.discount) * self.tax_rate)

    @property
    def total(self) -> Decimal:
        return self.subtotal - self.discount + self.tax


def build_quote(lines: Iterable[QuoteLine], tax_rate: Decimal = DEFAULT_TAX_RATE) -> Quote:
    return Quote(lines=tuple(lines), tax_rate=tax_rate)


def money(amount: Decimal) -> str:
    """Serialise an amount for a response body."""
    return f"{round_money(amount):.2f}"
