from decimal import Decimal

import pytest

from ledger_cli.money import average, format_money, parse_amount, quantize, split_evenly, total


def test_parse_amount_variants():
    assert parse_amount("-42.50") == Decimal("-42.50")
    assert parse_amount("+3") == Decimal("3.00")
    assert parse_amount("1,250.00") == Decimal("1250.00")
    assert parse_amount("-$9.99") == Decimal("-9.99")


@pytest.mark.parametrize("text", ["", "abc", "12,34", "--5", "$"])
def test_parse_amount_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_quantize_rounds_to_cents():
    assert quantize(Decimal("2.678")) == Decimal("2.68")
    assert quantize(Decimal("2.671")) == Decimal("2.67")
    assert quantize(7) == Decimal("7.00")


def test_format_money():
    assert format_money(Decimal("-1234.5")) == "-$1,234.50"
    assert format_money(Decimal("0")) == "$0.00"
    assert format_money(Decimal("99.99"), "eur") == "€99.99"
    assert format_money(Decimal("12"), "CHF") == "12.00 CHF"


def test_total_and_average():
    assert total([]) == Decimal("0.00")
    assert total([Decimal("1.10"), Decimal("2.20")]) == Decimal("3.30")
    assert average([Decimal("10"), Decimal("20")]) == Decimal("15.00")
    assert average([]) == Decimal("0.00")


def test_split_evenly():
    assert split_evenly(Decimal("10.00"), 3) == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum(split_evenly(Decimal("-0.05"), 2)) == Decimal("-0.05")
    with pytest.raises(ValueError):
        split_evenly(Decimal("1"), 0)
