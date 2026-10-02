from __future__ import annotations

from swe_agent.agents.loop import KEEP_RECENT_OUTPUTS, compact
from swe_agent.llm.types import Message


def _convo(n_outputs: int, size: int) -> list[Message]:
    msgs = [Message(role="system", content="S" * 100), Message(role="user", content="T" * 100)]
    for i in range(n_outputs):
        msgs.append(Message(role="assistant", content=f'{{"tool": "read_file", "i": {i}}}'))
        msgs.append(Message(role="user", content=f'<tool_output tool="read_file">{"x" * size}'))
    return msgs


def test_no_compaction_under_limit() -> None:
    msgs = _convo(3, 100)
    assert compact(msgs, 100_000) is msgs


def test_elides_oldest_outputs_keeps_recent_and_task() -> None:
    msgs = _convo(12, 5_000)
    out = compact(msgs, 30_000)
    assert out[0] == msgs[0] and out[1] == msgs[1]
    outputs = [m for m in out if m.role == "user"][1:]
    elided = [m for m in outputs if m.content.startswith("[earlier tool output elided")]
    assert elided and len(elided) <= 12 - KEEP_RECENT_OUTPUTS
    assert all("x" * 5000 in m.content for m in outputs[-KEEP_RECENT_OUTPUTS:])
    assert sum(len(m.content) for m in out) <= 30_000 + KEEP_RECENT_OUTPUTS * 5_100
    assert msgs[3].content.startswith("<tool_output")  # original list untouched
