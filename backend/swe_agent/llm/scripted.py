"""Deterministic scripted provider.

Used by the test suite and the offline demo to drive the full graph without a model server.
It is NOT a model: every response is supplied by the caller. Results produced with it must never
be reported as model performance.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolCall, ToolSchema, Usage


@dataclass
class ScriptedCall:
    model: str
    messages: list[Message]
    tools: list[ToolSchema] | None
    json_schema: dict[str, Any] | None


Responder = Callable[[ScriptedCall], "LLMResponse | dict[str, Any] | str"]
ScriptItem = LLMResponse | dict[str, Any] | str | Responder


@dataclass
class ScriptedProvider:
    script: list[ScriptItem]
    name: str = "scripted"
    calls: list[ScriptedCall] = field(default_factory=list)
    usage: Usage | None = None  # optional fixed usage per call (for budget tests)

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str,
        params: GenParams,
        tools: list[ToolSchema] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        call = ScriptedCall(model, list(messages), tools, json_schema)
        self.calls.append(call)
        if not self.script:
            raise AssertionError(f"scripted provider exhausted after {len(self.calls)} calls")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, LLMResponse | dict | str):
            item = item(call)
        if isinstance(item, LLMResponse):
            resp = item
        elif isinstance(item, dict):
            resp = LLMResponse(content=json.dumps(item))
        else:
            resp = LLMResponse(content=str(item))
        resp.model = resp.model or model
        if self.usage is not None:
            resp.usage = self.usage
        return resp

    async def aclose(self) -> None:
        return None


def action(tool: str, thought: str = "", **args: Any) -> dict[str, Any]:
    """Build a json_action response for a scripted tool loop step."""
    return {"thought": thought or f"call {tool}", "tool": tool, "args": args}


def native_call(tool: str, **args: Any) -> LLMResponse:
    return LLMResponse(content="", tool_calls=[ToolCall(id=f"c_{tool}", name=tool, arguments=args)])
