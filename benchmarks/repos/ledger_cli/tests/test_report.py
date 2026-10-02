from decimal import Decimal

import pytest

from ledger_cli.accounts import Ledger
from ledger_cli.report import category_totals, monthly_summary, render_table


def test_monthly_income_and_expenses(ledger_file):
    rows = monthly_summary(Ledger.from_text(ledger_file.read_text()))
    assert [(r.month, r.income, r.expenses) for r in rows] == [
        ("2024-01", Decimal("3000.00"), Decimal("66.50")),
        ("2024-02", Decimal("3000.00"), Decimal("1285.25")),
    ]


def test_monthly_net_without_spending():
    rows = monthly_summary(Ledger.from_text("2024-04-01 salary 250 #income\n"))
    assert rows[0].net == Decimal("250.00")


def test_category_totals(ledger_file):
    ledger = Ledger.from_text(ledger_file.read_text())
    assert category_totals(ledger.entries) == [
        ("housing", Decimal("1200.00")),
        ("food", Decimal("127.75")),
        ("fun", Decimal("24.00")),
    ]


def test_category_totals_ties_sorted_by_name():
    ledger = Ledger.from_text("2024-01-01 x -5 #zeta\n2024-01-02 y -5 #alpha\n")
    assert category_totals(ledger.entries) == [
        ("alpha", Decimal("5.00")),
        ("zeta", Decimal("5.00")),
    ]


def test_render_table():
    table = render_table(["name", "value"], [["a", "1"], ["long", "100"]])
    assert table.splitlines() == ["name  value", "----  -----", "a         1", "long    100"]
    with pytest.raises(ValueError):
        render_table(["a", "b"], [["only one"]])
