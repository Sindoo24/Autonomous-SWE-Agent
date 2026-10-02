from decimal import Decimal

from ledger_cli import main
from ledger_cli.accounts import Ledger
from ledger_cli.report import monthly_summary

LEDGER = """\
2024-01-02 salary 3000.00 #income
2024-01-05 groceries -42.50 #food
2024-01-09 cinema -24.00 #fun
2024-02-03 rent -1,200.00 #housing
"""


def test_monthly_net_is_income_minus_expenses():
    rows = monthly_summary(Ledger.from_text(LEDGER))
    assert [(r.month, r.net) for r in rows] == [
        ("2024-01", Decimal("2933.50")),
        ("2024-02", Decimal("-1200.00")),
    ]


def test_monthly_command_output(tmp_path, capsys):
    path = tmp_path / "household.ledger"
    path.write_text(LEDGER, encoding="utf-8")
    assert main([str(path), "monthly"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2].split() == ["2024-01", "$3,000.00", "$66.50", "$2,933.50"]
    assert lines[3].split() == ["2024-02", "$0.00", "$1,200.00", "-$1,200.00"]
