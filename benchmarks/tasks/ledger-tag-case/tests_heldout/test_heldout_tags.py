from decimal import Decimal

from ledger_cli import main
from ledger_cli.accounts import Ledger
from ledger_cli.report import category_totals

LEDGER = """\
2024-01-05 groceries -42.50 #food
2024-01-12 bakery -8.00 #Food
2024-01-20 market -10.00 #FOOD #Weekly
"""


def test_categories_ignore_tag_case(tmp_path, capsys):
    path = tmp_path / "jan.ledger"
    path.write_text(LEDGER, encoding="utf-8")
    assert main([str(path), "categories"]) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()[2:]]
    assert rows == [["food", "$60.50"]]


def test_register_shows_lowercase_category(tmp_path, capsys):
    path = tmp_path / "jan.ledger"
    path.write_text(LEDGER, encoding="utf-8")
    assert main([str(path), "register"]) == 0
    categories = [line.split()[2] for line in capsys.readouterr().out.splitlines()[2:]]
    assert categories == ["food", "food", "food"]


def test_tags_differing_only_in_case_collapse():
    ledger = Ledger.from_text("2024-02-01 lunch -9.00 #Work #work #LUNCH\n")
    entry = ledger.entries[0]
    assert entry.tags == ("work", "lunch")
    assert category_totals(ledger.entries) == [("work", Decimal("9.00"))]
