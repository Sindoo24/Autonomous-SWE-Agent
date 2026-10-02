from datetime import datetime, timedelta, timezone

import pytest

from eventlog import aggregate, filters
from eventlog.events import Event
from eventlog.export import format_timestamp, to_csv, to_json_lines
from eventlog.parse import parse_lines
from eventlog.pipeline import Pipeline, run_pipeline
from eventlog.sessions import sessionize


def ev(minute, second=0, level="INFO", source="app", message="m", duration=None, user=None, **fields):
    ts = datetime(2024, 5, 1, 12, minute, second, tzinfo=timezone.utc)
    return Event(ts, level, source, message, duration, user, fields)


def test_count_by_level_is_ordered_by_severity():
    events = [ev(0, level="ERROR"), ev(0), ev(1, level="DEBUG"), ev(2)]
    assert list(aggregate.count_by_level(events).items()) == [("DEBUG", 1), ("INFO", 2), ("ERROR", 1)]


def test_per_minute_buckets():
    events = [ev(0, 5), ev(0, 59), ev(2, 0)]
    buckets = aggregate.per_minute(events)
    assert list(buckets.values()) == [2, 1]
    assert list(buckets)[1] == datetime(2024, 5, 1, 12, 2, tzinfo=timezone.utc)


def test_percentile_interpolates():
    values = [10, 20, 30, 40]
    assert aggregate.percentile(values, 50) == 25
    assert aggregate.percentile(values, 100) == 40
    assert aggregate.percentile([7], 95) == 7
    with pytest.raises(ValueError):
        aggregate.percentile(values, 101)


def test_duration_stats():
    stats = aggregate.duration_stats([ev(0, duration=d) for d in (100.0, 200.0, 300.0)])
    assert stats["count"] == 3
    assert stats["mean"] == 200.0
    assert stats["p50"] == 200.0
    assert (stats["min"], stats["max"]) == (100.0, 300.0)


def test_top_sources_and_error_rate():
    events = [ev(0, source="b"), ev(0, source="a"), ev(0, source="b", level="ERROR"), ev(0, source="a")]
    assert aggregate.top_sources(events, 1) == [("a", 2)]
    assert aggregate.error_rate(events) == 0.25


def test_min_level_keeps_more_severe_levels():
    events = [ev(0, level=lvl) for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")]
    kept = filters.apply(events, filters.min_level("warn"))
    assert [e.level for e in kept] == ["WARNING", "ERROR", "CRITICAL"]


def test_source_and_time_filters():
    events = [ev(0, source="api"), ev(1, source="api.http"), ev(2, source="apiary"), ev(3, source="db")]
    kept = filters.apply(events, filters.from_sources(["api"]))
    assert [e.source for e in kept] == ["api", "api.http"]
    window = filters.between(events[1].timestamp, events[3].timestamp)
    assert [e.source for e in filters.apply(events, window)] == ["api.http", "apiary"]


def test_sessionize_splits_on_gap():
    events = [ev(0, user="a"), ev(10, user="a"), ev(50, user="a"), ev(5, user="b"), ev(7)]
    sessions = sessionize(events, gap=timedelta(minutes=20))
    assert [(s.key, len(s)) for s in sessions] == [("a", 2), ("b", 1), ("a", 1)]
    assert sessions[0].duration == timedelta(minutes=10)


def test_csv_export_quotes_and_blanks():
    event = ev(0, message='said "hi", then left', duration=12.0, status=200)
    out = to_csv([event], columns=("timestamp", "message", "duration_ms", "user", "status"))
    assert out.splitlines() == [
        "timestamp,message,duration_ms,user,status",
        '2024-05-01T12:00:00Z,"said ""hi"", then left",12,,200',
    ]


def test_json_lines_export():
    out = to_json_lines([ev(0, user="u", region="eu")])
    assert out == ('{"timestamp": "2024-05-01T12:00:00Z", "level": "INFO", "source": "app", '
                   '"message": "m", "user": "u", "region": "eu"}\n')


def test_format_timestamp_milliseconds():
    ts = datetime(2024, 5, 1, 12, 0, 0, 250000, tzinfo=timezone.utc)
    assert format_timestamp(ts) == "2024-05-01T12:00:00.250Z"


def test_run_pipeline_report(sample_lines):
    report = run_pipeline(sample_lines)
    assert report["events"] == 5
    assert report["skipped_lines"] == [6]
    assert report["levels"] == {"INFO": 3, "WARNING": 1, "ERROR": 1}
    assert list(report["per_minute"].values()) == [2, 2, 1]
    assert report["durations"]["max"] == 1500.0
    assert report["sessions"]["count"] == 3
    assert report["error_rate"] == 0.2


def test_pipeline_source_filter(sample_lines):
    report = Pipeline().where(filters.from_sources(["api.http"])).run(sample_lines)
    assert report["events"] == 3
    assert report["top_sources"] == [("api.http", 3)]


def test_parse_then_filter_by_field(sample_lines):
    events = parse_lines(sample_lines)
    assert len(filters.apply(events, filters.has_fields(status=200))) == 2
