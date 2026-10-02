"""LLM gateway: the only thing agent nodes talk to.

Responsibilities:
- role -> (provider, model, tool mode) binding from config
- budget enforcement (tokens, wall clock) before every call
- structured output with exactly one repair retry (syntactic or semantic errors)
- tool-action selection in either `json_action` or `native_tools` mode, yielding the same ToolCall
- recording every call (prompt/response as artifacts, usage, latency) to the trajectory
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from swe_agent.config import ToolMode
from swe_agent.core.budget import BudgetMeter
from swe_agent.core.errors import ModelError
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.schema_utils import inline_refs
from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolCall, ToolSchema
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder

T = TypeVar("T", bound=BaseModel)

SemanticValidator = Callable[[Any], list[str]]


class ModelRole(StrEnum):
    REASONING = "reasoning"
    CODER = "coder"


@dataclass(frozen=True)
class RoleBinding:
    provider: LLMProvider
    model: str
    tool_mode: ToolMode


@dataclass(frozen=True)
class Action:
    call: ToolCall
    thought: str
    assistant_message: Message


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from model text. Tolerates code fences and leading/trailing prose."""
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("no JSON object found", text, 0)


class LLMGateway:
    def __init__(
        self,
        bindings: dict[ModelRole, RoleBinding],
        params: GenParams,
        meter: BudgetMeter,
        recorder: TrajectoryRecorder,
        artifacts: ArtifactStore,
    ) -> None:
        self.bindings = bindings
        self.params = params
        self.meter = meter
        self.recorder = recorder
        self.artifacts = artifacts

    def tool_mode(self, role: ModelRole) -> ToolMode:
        return self.bindings[role].tool_mode

    # ------------------------------------------------------------------ low level

    async def _call(
        self,
        role: ModelRole,
        messages: list[Message],
        *,
        purpose: str,
        tools: list[ToolSchema] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.meter.check()
        binding = self.bindings[role]
        prompt_ref = self.artifacts.put_json(
            "prompt", [m.model_dump(exclude_defaults=True) for m in messages]
        )
        try:
            resp = await binding.provider.generate(
                messages,
                model=binding.model,
                params=self.params,
                tools=tools,
                json_schema=json_schema,
            )
        except ModelError as exc:
            self.recorder.record(
                "model_call",
                role=role.value,
                purpose=purpose,
                provider=binding.provider.name,
                model=binding.model,
                status="error",
                error=str(exc),
                prompt_artifact=prompt_ref.id,
            )
            raise
        response_ref = self.artifacts.put_json(
            "response",
            {"content": resp.content, "tool_calls": [tc.model_dump() for tc in resp.tool_calls]},
        )
        self.meter.add_tokens(resp.usage.total)
        self.recorder.record(
            "model_call",
            role=role.value,
            purpose=purpose,
            provider=binding.provider.name,
            model=resp.model or binding.model,
            status="ok",
            tokens_in=resp.usage.prompt_tokens,
            tokens_out=resp.usage.completion_tokens,
            latency_ms=resp.latency_ms,
            finish_reason=resp.finish_reason,
            prompt_chars=sum(len(m.content) for m in messages),
            prompt_artifact=prompt_ref.id,
            response_artifact=response_ref.id,
        )
        return resp

    # ------------------------------------------------------------------ structured output

    async def structured(
        self,
        role: ModelRole,
        messages: list[Message],
        schema: type[T],
        *,
        purpose: str,
        validate: Callable[[T], list[str]] | None = None,
    ) -> T:
        """Return a validated `schema` instance. One repair attempt, then ModelError."""
        json_schema = inline_refs(schema.model_json_schema())
        convo = list(messages)
        last_errors: list[str] = []
        for attempt in range(2):
            resp = await self._call(
                role,
                convo,
                purpose=purpose if attempt == 0 else f"{purpose}:repair",
                json_schema=json_schema,
            )
            errors: list[str]
            try:
                obj = schema.model_validate(extract_json(resp.content))
            except json.JSONDecodeError as exc:
                errors = [f"output is not valid JSON: {exc.msg}"]
            except ValidationError as exc:
                errors = [
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                ][:10]
            else:
                errors = validate(obj) if validate else []
                if not errors:
                    return obj
            last_errors = errors
            convo = [
                *convo,
                Message(role="assistant", content=resp.content),
                Message(
                    role="user",
                    content=(
                        "Your previous output was rejected:\n- "
                        + "\n- ".join(errors)
                        + "\nReturn a corrected JSON object only, matching the schema."
                    ),
                ),
            ]
        raise ModelError(f"{purpose}: invalid structured output after repair: {last_errors}")

    # ------------------------------------------------------------------ tool actions

    @staticmethod
    def action_schema(tools: list[ToolSchema]) -> dict[str, Any]:
        """JSON schema for json_action mode: one object per tool, discriminated by `tool`."""
        variants = [
            {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "maxLength": 600},
                    "tool": {"type": "string", "enum": [t.name]},
                    "args": t.parameters,
                },
                "required": ["thought", "tool", "args"],
            }
            for t in tools
        ]
        return variants[0] if len(variants) == 1 else {"anyOf": variants}

    async def next_action(
        self, role: ModelRole, messages: list[Message], tools: list[ToolSchema], *, purpose: str
    ) -> Action:
        mode = self.tool_mode(role)
        names = {t.name for t in tools}
        convo = list(messages)
        problem = ""
        for attempt in range(2):
            p = purpose if attempt == 0 else f"{purpose}:repair"
            if mode is ToolMode.NATIVE_TOOLS:
                resp = await self._call(role, convo, purpose=p, tools=tools)
                if resp.tool_calls:
                    call = resp.tool_calls[0]
                    if call.name in names:
                        return Action(
                            call=call,
                            thought=resp.content[:600],
                            assistant_message=Message(
                                role="assistant", content=resp.content, tool_calls=[call]
                            ),
                        )
                    problem = f"unknown tool {call.name!r}; available: {sorted(names)}"
                else:
                    problem = "you must call exactly one of the available tools"
            else:
                resp = await self._call(
                    role, convo, purpose=p, json_schema=self.action_schema(tools)
                )
                try:
                    data = extract_json(resp.content)
                    tool = data["tool"]
                    args = data.get("args", {})
                    if tool not in names:
                        raise KeyError(f"unknown tool {tool!r}; available: {sorted(names)}")
                    if not isinstance(args, dict):
                        raise TypeError("args must be an object")
                    call = ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=tool, arguments=args)
                    return Action(
                        call=call,
                        thought=str(data.get("thought", ""))[:600],
                        assistant_message=Message(role="assistant", content=resp.content),
                    )
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    problem = f"invalid action: {exc}"
            convo = [
                *convo,
                Message(role="assistant", content=resp.content),
                Message(
                    role="user",
                    content=f"Your previous response was rejected: {problem}. "
                    "Respond with exactly one tool call.",
                ),
            ]
        raise ModelError(f"{purpose}: model did not produce a valid tool call: {problem}")

    def tool_result_message(self, role: ModelRole, call: ToolCall, content: str) -> Message:
        """Tool results go back as `tool` messages natively, or as user messages in json_action."""
        if self.tool_mode(role) is ToolMode.NATIVE_TOOLS:
            return Message(role="tool", content=content, tool_call_id=call.id, name=call.name)
        return Message(role="user", content=content)
