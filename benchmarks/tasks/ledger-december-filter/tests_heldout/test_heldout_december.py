from datetime import date

from ledger_cli import main
from ledger_cli.accounts import Ledger, month_bounds

LEDGER = """\
2024-11-28 groceries -50.00 #food
2024-12-01 coffee -3.00 #food
2024-12-24 gifts -300.00 #gifts
2024-12-31 party -45.00 #fun
2025-01-01 salary 2000 #income
"""


def test_december_balance(tmp_path, capsys):
    path = tmp_path / "2024.ledger"
    path.write_text(LEDGER, encoding="utf-8")
    assert main([str(path), "balance", "--month", "2024-12"]) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()[2:]]
    assert rows == [["checking", "-$348.00"], ["total", "-$348.00"]]


def test_december_bounds():
    assert month_bounds("2024-12") == (date(2024, 12, 1), date(2024, 12, 31))


def test_for_month_december_excludes_neighbours():
    ledger = Ledger.from_text(LEDGER)
    assert [e.description for e in ledger.for_month("2024-12")] == ["coffee", "gifts", "party"]
    assert [e.description for e in ledger.for_month("2024-11")] == ["groceries"]
