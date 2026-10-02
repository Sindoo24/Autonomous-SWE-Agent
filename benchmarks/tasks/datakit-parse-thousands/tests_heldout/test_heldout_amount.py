from decimal import Decimal

from datakit.parsing import parse_amount


def test_thousands_separator():
    assert parse_amount("1,234.50") == Decimal("1234.50")


def test_thousands_in_parentheses():
    assert parse_amount("(1,000.00)") == Decimal("-1000.00")


def test_currency_and_thousands():
    assert parse_amount("$ 12,000") == Decimal("12000")
