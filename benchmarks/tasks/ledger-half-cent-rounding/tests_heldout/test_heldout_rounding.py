from ledger_cli import main


def _run(tmp_path, capsys, text, *args):
    path = tmp_path / "rounding.ledger"
    path.write_text(text, encoding="utf-8")
    assert main([str(path), *args]) == 0
    return capsys.readouterr().out


def test_summary_average_rounds_half_up(tmp_path, capsys):
    text = "2024-01-03 lunch -20.25 #food\n2024-02-01 salary 100.00 #income\n"
    out = _run(tmp_path, capsys, text, "summary")
    assert "average monthly spending: $10.13" in out
    assert "balance: $79.75" in out


def test_sub_cent_amount_in_balance(tmp_path, capsys):
    text = "2024-03-01 fuel -30.125 #car\n2024-03-02 snack -0.245 #food\n"
    out = _run(tmp_path, capsys, text, "balance")
    assert out.splitlines()[-1].split() == ["total", "-$30.38"]


def test_register_amount_rounding(tmp_path, capsys):
    out = _run(tmp_path, capsys, "2024-03-01 fuel -30.125 #car\n", "register")
    assert out.splitlines()[2].split()[3] == "-$30.13"
