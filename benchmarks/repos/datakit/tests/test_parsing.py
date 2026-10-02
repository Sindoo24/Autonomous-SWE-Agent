from decimal import Decimal

import pytest

from datakit.parsing import ParseError, parse_amount, parse_bool


def test_parse_plain_amount():
    assert parse_amount("12.50") == Decimal("12.50")


def test_parse_negative_amount():
    assert parse_amount("-3.00") == Decimal("-3.00")


def test_parse_amount_rejects_garbage():
    with pytest.raises(ParseError):
        parse_amount("twelve")


def test_parse_bool():
    assert parse_bool("Yes") is True and parse_bool("0") is False
