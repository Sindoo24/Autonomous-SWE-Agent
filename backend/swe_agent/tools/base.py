"""Tool framework: typed specs, per-call context, structured results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from swe_agent.repository.pathjail import WritePolicy
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import Workspace

Effect = Literal["read", "write", "exec"]


class ToolInput(BaseModel):
    """Base for tool argument models: unknown arguments are an error, not silently ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ToolOutput(BaseModel):
    def render(self) -> str:  # what the model sees
        raise NotImplementedError


InT = TypeVar("InT", bound=ToolInput)
OutT = TypeVar("OutT", bound=ToolOutput)


class EditRecord(BaseModel):
    tool: str
    path: str
    lines_before: int
    lines_after: int
    note: str = ""


@dataclass
class ToolContext:
    workspace: Workspace
    index: SymbolIndex
    node: str
    write_policy: WritePolicy = field(default_factory=lambda: WritePolicy(allow_source=False))
    max_read_lines: int = 300
    edits: list[EditRecord] = field(default_factory=list)
    # Only the ReAct baseline gets a test runner; the agent's nodes never run tests
    # through a tool (execution is a graph node there).
    test_runner: Callable[[list[str]], str] | None = None


@dataclass(frozen=True)
class ToolSpec(Generic[InT, OutT]):
    name: str
    description: str
    input_model: type[InT]
    effect: Effect
    handler: Callable[[ToolContext, InT], OutT]
    timeout_s: float = 30.0

    def json_schema(self) -> dict[str, Any]:
        from swe_agent.llm.schema_utils import inline_refs

        schema = inline_refs(self.input_model.model_json_schema())
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return schema


class ToolError(BaseModel):
    type: str
    message: str


class ToolResult(BaseModel):
    tool: str
    ok: bool
    output: str  # rendered (and possibly truncated) text for the model
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    truncated: bool = False
    repeated: bool = False
    duration_ms: int = 0
