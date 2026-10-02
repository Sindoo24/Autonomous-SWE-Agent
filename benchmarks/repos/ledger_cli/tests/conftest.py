import pytest

SAMPLE = """\
; household ledger
2024-01-02 salary 3000.00 #income
2024-01-05 groceries at market -42.50 #food
2024-01-09 cinema -24.00 #fun @credit
2024-02-01 salary 3000.00 #income
2024-02-03 rent -1,200.00 #housing
2024-02-14 dinner -85.25 #food @credit
"""


@pytest.fixture
def ledger_file(tmp_path):
    path = tmp_path / "household.ledger"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


@pytest.fixture
def write_ledger(tmp_path):
    def _write(text, name="custom.ledger"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write
