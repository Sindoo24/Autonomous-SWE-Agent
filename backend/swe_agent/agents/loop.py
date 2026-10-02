"""Bounded tool loop used by the explore and implement nodes.

The model picks one tool per turn; `finish` is a pseudo-tool whose arguments are the node's typed
output. The loop enforces a step limit, validates the finish payload (the model gets the errors
and may retry), and elides old tool outputs when the conversation grows too large.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from swe_agent.agents.context import RunContext
from swe_agent.config import ToolMode
from swe_agent.core.errors import ModelError
from swe_agent.llm.gateway import ModelRole
from swe_agent.llm.schema_utils import inline_refs
from swe_agent.llm.types import Message, ToolSchema
from swe_agent.prompts.templates import (
    ACTION_FORMAT_JSON,
    ACTION_FORMAT_NATIVE,
    tool_output_block,
)
from swe_agent.tools.base import ToolContext

T = TypeVar("T", bound=BaseModel)

KEEP_RECENT_OUTPUTS = 6
FORCED_FINISH_ATTEMPTS = 2


def compact(messages: list[Message], max_chars: int) -> list[Message]:
    """Elide the oldest tool outputs once the conversation exceeds `max_chars`.

    The system prompt, the task message and the most recent outputs are always kept whole.
    """
    total = sum(len(m.content) for m in messages)
    if total <= max_chars:
        return messages
    out = list(messages)
    # Indices of tool-result messages (role tool, or wrapped user messages after the task).
    result_idx = [
        i
        for i, m in enumerate(out)
        if i >= 2 and (m.role == "tool" or (m.role == "user" and "<tool_output" in m.content))
    ]
    for i in result_idx[:-KEEP_RECENT_OUTPUTS]:
        if total <= max_chars:
            break
        old = out[i]
        stub = f"[earlier tool output elided to save context: {len(old.content)} chars]"
        total -= len(old.content) - len(stub)
        out[i] = old.model_copy(update={"content": stub})
    return out


async def run_tool_loop(
    ctx: RunContext,
    *,
    node: str,
    role: ModelRole,
    system: str,
    task: str,
    finish_model: type[T],
    finish_description: str,
    max_steps: int,
    tool_ctx: ToolContext,
    finish_validate: Callable[[T], list[str]] | None = None,
) -> T:
    gw, ex = ctx.gateway, ctx.executor
    fmt = (
        ACTION_FORMAT_NATIVE if gw.tool_mode(role) is ToolMode.NATIVE_TOOLS else ACTION_FORMAT_JSON
    )
    finish_tool = ToolSchema(
        name="finish",
        description=finish_description,
        parameters=inline_refs(finish_model.model_json_schema()),
    )
    all_tools = ex.schemas_for(node, extra=[finish_tool])
    messages = [
        Message(role="system", content=system.format(max_steps=max_steps, action_format=fmt)),
        Message(role="user", content=task),
    ]

    for step in range(max_steps + FORCED_FINISH_ATTEMPTS):
        forced = step >= max_steps
        tools = [finish_tool] if forced else all_tools
        if forced and step == max_steps:
            note = (
                "Tool budget for this stage is used up. "
                "Call `finish` now with the best result you have."
            )
            last = messages[-1]
            if last.role == "user":
                # Keep strict user/assistant alternation for chat templates that require it.
                messages[-1] = last.model_copy(update={"content": f"{last.content}\n\n{note}"})
            else:
                messages.append(Message(role="user", content=note))
        action = await gw.next_action(
            role,
            compact(messages, ctx.settings.loop_context_max_chars),
            tools,
            purpose=f"{node}.step{step + 1}",
        )
        messages.append(action.assistant_message)
        call = action.call

        if call.name == "finish":
            errors: list[str]
            try:
                obj = finish_model.model_validate(call.arguments)
            except ValidationError as exc:
                errors = [
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                ][:10]
            else:
                errors = finish_validate(obj) if finish_validate else []
                if not errors:
                    ctx.recorder.record(
                        "tool_call",
                        tool="finish",
                        system=True,
                        status="ok",
                        duration_ms=0,
                        args={},
                        step=step + 1,
                    )
                    return obj
            ctx.recorder.record(
                "tool_call",
                tool="finish",
                system=True,
                status="error",
                duration_ms=0,
                args={},
                error="; ".join(errors)[:1000],
            )
            feedback = "finish rejected:\n- " + "\n- ".join(errors)
            messages.append(gw.tool_result_message(role, call, feedback))
            continue

        result = await ex.invoke(call.name, call.arguments, tool_ctx)
        messages.append(
            gw.tool_result_message(role, call, tool_output_block(call.name, result.output))
        )

    raise ModelError(f"{node}: no valid `finish` within {max_steps} steps")
