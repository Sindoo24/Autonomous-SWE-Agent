"""eventlog: parse, filter, aggregate and export application log events.

Typical use::

    from eventlog.pipeline import run_pipeline
    report = run_pipeline(open("app.log"), min_level="WARNING")
"""

from eventlog.events import Event

__all__ = ["Event"]
__version__ = "1.4.0"
