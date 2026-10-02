from datetime import date
from decimal import Decimal

import pytest

from ledger_cli.parser import Entry, ParseError, parse_ledger, parse_line


def test_parse_basic_line():
    entry = parse_line("2024-01-05 groceries -42.50 #food")
    assert entry == Entry(date(2024, 1, 5), "groceries", Decimal("-42.50"), ("food",), "checking")
    assert entry.category == "food"
    assert entry.is_expense
    assert entry.month == "2024-01"


def test_multi_word_description_account_and_tags():
    entry = parse_line("2024-03-10 coffee with sam -4.25 #food #social @credit")
    assert entry.description == "coffee with sam"
    assert entry.tags == ("food", "social")
    assert entry.account == "credit"


def test_untagged_entry_is_uncategorized():
    entry = parse_line("2024-03-11 refund 15")
    assert entry.category == "uncategorized"
    assert not entry.is_expense


def test_trailing_comment_is_ignored():
    entry = parse_line("2024-03-12 lunch -12.00 #food ; with the team")
    assert entry.amount == Decimal("-12.00")
    assert entry.tags == ("food",)


def test_parse_ledger_skips_comments_and_blanks():
    text = "; header\n\n2024-01-01 salary 100 #income\n   \n2024-01-02 tea -2 #food\n"
    entries = parse_ledger(text)
    assert [e.description for e in entries] == ["salary", "tea"]


def test_parse_errors_report_line_number():
    with pytest.raises(ParseError) as info:
        parse_ledger("2024-01-01 salary 100\n2024-13-01 bad -1\n")
    assert info.value.line_no == 2
    with pytest.raises(ParseError):
        parse_line("2024-01-01 coffee")
    with pytest.raises(ParseError):
        parse_line("2024-01-01 coffee -3 #")
