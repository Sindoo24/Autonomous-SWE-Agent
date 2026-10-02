from datetime import datetime, timezone

from eventlog import aggregate
from eventlog.pipeline import run_pipeline

LINES = [
    "2024-05-01T12:00:01.250Z INFO [api] a",
    "2024-05-01T12:00:30Z INFO [api] b",
    "2024-05-01T12:00:59.900Z INFO [api] c",
    "2024-05-01T12:01:00.001Z INFO [api] d",
]


def test_fractional_seconds_share_a_minute_bucket():
    report = run_pipeline(LINES)
    assert report["per_minute"] == {
        datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc): 3,
        datetime(2024, 5, 1, 12, 1, tzinfo=timezone.utc): 1,
    }


def test_bucket_keys_are_whole_minutes():
    report = run_pipeline(LINES)
    for key in report["per_minute"]:
        assert key.second == 0 and key.microsecond == 0


def test_bucket_counts_add_up():
    report = run_pipeline(LINES)
    assert sum(report["per_minute"].values()) == report["events"] == 4
