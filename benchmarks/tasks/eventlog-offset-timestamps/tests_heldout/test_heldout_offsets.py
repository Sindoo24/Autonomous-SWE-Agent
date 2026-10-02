from datetime import datetime, timedelta, timezone

from eventlog.pipeline import Pipeline, run_pipeline

UTC = timezone.utc
LINES = [
    "2024-05-01T14:00:00+02:00 INFO [web] login user=carol",
    "2024-05-01T12:10:00Z INFO [api] view user=carol",
    "2024-05-01T12:20:00Z INFO [api] logout user=carol",
]


def test_mixed_offsets_form_one_session():
    report = run_pipeline(LINES)
    assert report["sessions"]["count"] == 1
    assert report["sessions"]["longest_seconds"] == 20 * 60


def test_report_range_and_buckets_in_utc():
    report = run_pipeline(LINES + ['{"ts": "2024-05-01T08:15:00-04:00", "level": "info", "msg": "x"}'])
    assert report["first"] == datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
    assert report["last"] == datetime(2024, 5, 1, 12, 20, tzinfo=UTC)
    assert list(report["per_minute"]) == [
        datetime(2024, 5, 1, 12, m, tzinfo=UTC) for m in (0, 10, 15, 20)
    ]


def test_naive_timestamps_use_default_timezone():
    ist = timezone(timedelta(hours=5, minutes=30))
    events = Pipeline(default_tz=ist).select(["2024-05-01T17:30:00 INFO [batch] start"])
    assert events[0].timestamp == datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
    assert events[0].timestamp.utcoffset() == timedelta(0)
