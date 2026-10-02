from eventlog import filters
from eventlog.events import Event
from eventlog.parse import parse_lines
from eventlog.pipeline import run_pipeline

LINES = [
    "2024-05-01T12:00:00Z DEBUG [api] cache warm",
    "2024-05-01T12:00:01Z INFO [api] request",
    "2024-05-01T12:00:02Z WARNING [api] slow",
    "2024-05-01T12:00:03Z ERROR [api] failed",
    "2024-05-01T12:00:04Z CRITICAL [db] down",
]


def test_warning_threshold_includes_errors():
    report = run_pipeline(LINES, min_level="warning")
    assert report["levels"] == {"WARNING": 1, "ERROR": 1, "CRITICAL": 1}


def test_error_threshold_excludes_info():
    report = run_pipeline(LINES, min_level="err")
    assert report["levels"] == {"ERROR": 1, "CRITICAL": 1}


def test_debug_threshold_keeps_everything():
    events = parse_lines(LINES)
    assert filters.apply(events, filters.min_level("debug")) == events
