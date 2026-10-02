from datetime import datetime, timezone

import pytest

from eventlog.parse import ParseError, parse_duration, parse_json_line, parse_line, parse_lines, parse_timestamp


def test_parse_timestamp_zulu():
    assert parse_timestamp("2024-05-01T12:00:03Z") == datetime(2024, 5, 1, 12, 0, 3, tzinfo=timezone.utc)


def test_parse_timestamp_naive_defaults_to_utc():
    assert parse_timestamp("2024-05-01 08:30:00") == datetime(2024, 5, 1, 8, 30, tzinfo=timezone.utc)


def test_parse_timestamp_epoch():
    assert parse_timestamp("0") == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(ParseError):
        parse_timestamp("yesterday")


@pytest.mark.parametrize(
    "text, expected", [("250ms", 250.0), ("1.5s", 1500.0), ("80us", 0.08), ("12", 12.0), ("2m", 120000.0)]
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == pytest.approx(expected)


def test_parse_line_extracts_fields():
    event = parse_line("2024-05-01T12:00:03Z warn [api.db] slow query on orders table=orders rows=12 duration=1.5s")
    assert event.level == "WARNING"
    assert event.source == "api.db"
    assert event.message == "slow query on orders"
    assert event.fields == {"table": "orders", "rows": 12}
    assert event.duration_ms == 1500.0
    assert event.user is None


def test_parse_json_line():
    event = parse_json_line('{"timestamp": "2024-05-01T12:00:00Z", "level": "fatal", "source": "db", '
                            '"message": "down", "user": "ops", "region": "eu"}')
    assert (event.level, event.source, event.message, event.user) == ("CRITICAL", "db", "down", "ops")
    assert event.fields == {"region": "eu"}


def test_parse_lines_skips_and_records_errors(sample_lines):
    errors = []
    events = parse_lines(sample_lines, errors=errors)
    assert len(events) == 5
    assert errors and errors[0][0] == 6


def test_parse_lines_strict(sample_lines):
    with pytest.raises(ParseError, match="line 6"):
        parse_lines(sample_lines, strict=True)


def test_unknown_level_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_line("2024-05-01T12:00:03Z LOUD [x] hello")
