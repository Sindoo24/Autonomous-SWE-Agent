import pytest

SAMPLE_LOG = """\
# service log excerpt
2024-05-01T12:00:03Z INFO [api.http] GET /orders status=200 duration=120ms user=alice
2024-05-01T12:00:41Z info [api.http] GET /orders/7 status=200 duration=80ms user=alice
2024-05-01T12:01:10Z WARN [api.db] slow query table=orders duration=1.5s
{"ts": "2024-05-01T12:01:30Z", "level": "error", "logger": "api.http", "msg": "POST /orders failed", "status": 500, "duration_ms": 300, "user": "bob"}
this line is garbage
2024-05-01T12:45:00Z INFO [worker] job finished duration=40ms user=alice
"""


@pytest.fixture
def sample_lines():
    return SAMPLE_LOG.splitlines()
