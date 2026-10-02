from ledger_cli import main


def _rows(output):
    return {line.split()[0]: line.split()[1:] for line in output.splitlines()[2:] if line.strip()}


def test_balance_command(ledger_file, capsys):
    assert main([str(ledger_file), "balance"]) == 0
    rows = _rows(capsys.readouterr().out)
    assert rows["checking"] == ["$4,757.50"]
    assert rows["credit"] == ["-$109.25"]
    assert rows["total"] == ["$4,648.25"]


def test_balance_for_month_and_account(ledger_file, capsys):
    assert main([str(ledger_file), "balance", "--month", "2024-01", "--account", "credit"]) == 0
    rows = _rows(capsys.readouterr().out)
    assert rows == {"credit": ["-$24.00"], "total": ["-$24.00"]}


def test_categories_top(ledger_file, capsys):
    assert main([str(ledger_file), "categories", "--top", "2"]) == 0
    rows = _rows(capsys.readouterr().out)
    assert rows == {"housing": ["$1,200.00"], "food": ["$127.75"]}


def test_register_running_balance(ledger_file, capsys):
    assert main([str(ledger_file), "register", "--account", "credit"]) == 0
    lines = capsys.readouterr().out.splitlines()[2:]
    assert lines[0].split()[-1] == "-$24.00"
    assert lines[1].split()[-1] == "-$109.25"


def test_summary(ledger_file, capsys):
    assert main([str(ledger_file), "summary"]) == 0
    out = capsys.readouterr().out
    assert "entries: 6" in out
    assert "months: 2 (2024-01 to 2024-02)" in out
    assert "average monthly spending: $675.88" in out


def test_split(ledger_file, capsys):
    assert main([str(ledger_file), "split", "--people", "3", "--month", "2024-01"]) == 0
    out = capsys.readouterr().out
    assert "total spending: $66.50" in out
    assert "person 1: $22.17" in out
    assert "person 3: $22.16" in out


def test_currency_option(ledger_file, capsys):
    assert main([str(ledger_file), "--currency", "EUR", "balance"]) == 0
    assert "€4,648.25" in capsys.readouterr().out


def test_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.ledger"), "balance"]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_bad_ledger_line(write_ledger, capsys):
    path = write_ledger("2024-01-01 salary 100\nnot a line\n")
    assert main([str(path), "balance"]) == 1
    assert "line 2" in capsys.readouterr().err
