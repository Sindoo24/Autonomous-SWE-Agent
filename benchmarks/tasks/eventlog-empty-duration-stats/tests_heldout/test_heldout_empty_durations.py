from eventlog import aggregate
from eventlog.pipeline import run_pipeline

LINES = [
    "2024-05-01T12:00:00Z INFO [api] started",
    "2024-05-01T12:00:05Z WARNING [api] slow start user=ops",
]
EMPTY = {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}


def test_pipeline_with_no_matching_events():
    report = run_pipeline(LINES, sources=["billing"])
    assert report["events"] == 0
    assert report["durations"] == EMPTY
    assert report["levels"] == {}


def test_pipeline_without_any_durations():
    report = run_pipeline(LINES)
    assert report["events"] == 2
    assert report["durations"] == EMPTY


def test_percentile_of_nothing_is_none():
    assert aggregate.percentile([], 50) is None
    assert aggregate.percentile([], 0) is None
