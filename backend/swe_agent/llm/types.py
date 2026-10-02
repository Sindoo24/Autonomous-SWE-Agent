"""Provider-neutral message and response types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name for role="tool"


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments object


class GenParams(BaseModel):
    temperature: float = 0.0
    max_tokens: int = 2048
    seed: int | None = None
    num_ctx: int | None = None
    think: bool | None = None


class Usage(BaseModel):
    # None means the provider did not report it. We never estimate silently.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    model: str = ""
    finish_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
