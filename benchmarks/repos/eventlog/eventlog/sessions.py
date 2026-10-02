"""Group events into sessions: runs of activity by one key separated by idle gaps."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Hashable, Iterable, Optional

from eventlog.events import Event

DEFAULT_GAP = timedelta(minutes=30)


@dataclass
class Session:
    key: Hashable
    events: list[Event] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.events[0].timestamp

    @property
    def end(self) -> datetime:
        return self.events[-1].timestamp

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def __len__(self) -> int:
        return len(self.events)


def by_user(event: Event) -> Optional[str]:
    return event.user


def sessionize(
    events: Iterable[Event],
    gap: timedelta = DEFAULT_GAP,
    key: Callable[[Event], Optional[Hashable]] = by_user,
) -> list[Session]:
    """Split each key's events into sessions.

    A new session starts when more than ``gap`` has passed since the previous event with the
    same key. Events whose key is ``None`` are ignored. Sessions are returned ordered by
    start time, then by key.
    """
    grouped: dict[Hashable, list[Event]] = defaultdict(list)
    for event in events:
        k = key(event)
        if k is not None:
            grouped[k].append(event)

    sessions: list[Session] = []
    for k, items in grouped.items():
        items.sort(key=lambda e: e.timestamp)
        current = Session(k, [items[0]])
        for previous, event in zip(items, items[1:]):
            if event.timestamp - previous.timestamp > gap:
                sessions.append(current)
                current = Session(k, [])
            current.events.append(event)
        sessions.append(current)
    sessions.sort(key=lambda s: (s.start, str(s.key)))
    return sessions


def session_summary(sessions: Iterable[Session]) -> dict[str, object]:
    sessions = list(sessions)
    if not sessions:
        return {"count": 0, "users": 0, "longest_seconds": None, "mean_events": None}
    return {
        "count": len(sessions),
        "users": len({s.key for s in sessions}),
        "longest_seconds": max(s.duration for s in sessions).total_seconds(),
        "mean_events": sum(len(s) for s in sessions) / len(sessions),
    }
