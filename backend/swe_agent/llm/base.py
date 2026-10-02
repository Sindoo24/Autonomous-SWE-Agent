"""The provider protocol. Agent code never imports a concrete provider; it uses the gateway."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolSchema


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str,
        params: GenParams,
        tools: list[ToolSchema] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """One chat completion.

        tools:       native function-calling schemas (native_tools mode).
        json_schema: constrain output to this JSON schema (json_action / structured output).
        """
        ...

    async def aclose(self) -> None: ...
