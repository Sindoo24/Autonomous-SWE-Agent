from decimal import Decimal

from ledger_cli import main
from ledger_cli.accounts import Ledger

LEDGER = """\
2024-03-01 rent for 2 months -1800.00 #housing
2024-03-02 salary 3000.00 #income
2024-03-05 dinner for 4 -120.00 #food
"""


def _run(tmp_path, capsys, *args):
    path = tmp_path / "march.ledger"
    path.write_text(LEDGER, encoding="utf-8")
    assert main([str(path), *args]) == 0
    return [line.split() for line in capsys.readouterr().out.splitlines()[2:]]


def test_balance_with_numbers_in_description(tmp_path, capsys):
    rows = _run(tmp_path, capsys, "balance")
    assert rows == [["checking", "$1,080.00"], ["total", "$1,080.00"]]


def test_categories_with_numbers_in_description(tmp_path, capsys):
    rows = _run(tmp_path, capsys, "categories")
    assert rows == [["housing", "$1,800.00"], ["food", "$120.00"]]


def test_description_keeps_numbers():
    entries = Ledger.from_text(LEDGER).entries
    assert [e.description for e in entries] == ["rent for 2 months", "salary", "dinner for 4"]
    assert [e.amount for e in entries] == [
        Decimal("-1800.00"),
        Decimal("3000.00"),
        Decimal("-120.00"),
    ]
