from datetime import date
from decimal import Decimal

import pytest

from ledger_cli.accounts import Ledger, month_bounds


def test_month_bounds():
    assert month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds("2023-06") == (date(2023, 6, 1), date(2023, 6, 30))
    with pytest.raises(ValueError):
        month_bounds("2024-13")
    with pytest.raises(ValueError):
        month_bounds("Jan 2024")


def test_accounts_and_balances(ledger_file):
    ledger = Ledger.from_text(ledger_file.read_text())
    accounts = ledger.accounts()
    assert list(accounts) == ["checking", "credit"]
    assert accounts["credit"].balance == Decimal("-109.25")
    assert ledger.balance() == Decimal("4648.25")
    assert ledger.balance("checking") == Decimal("4757.50")


def test_between_is_inclusive(ledger_file):
    ledger = Ledger.from_text(ledger_file.read_text())
    window = ledger.between(date(2024, 1, 5), date(2024, 2, 1))
    assert [e.description for e in window] == ["groceries at market", "cinema", "salary"]


def test_for_month_and_months(ledger_file):
    ledger = Ledger.from_text(ledger_file.read_text())
    assert ledger.months() == ["2024-01", "2024-02"]
    assert len(ledger.for_month("2024-02")) == 3
    assert len(ledger.for_account("CREDIT")) == 2


def test_entries_are_sorted_by_date():
    ledger = Ledger.from_text("2024-05-02 b -1\n2024-05-01 a -1\n2024-05-02 c -1\n")
    assert [e.description for e in ledger] == ["a", "b", "c"]
