"""Ollama provider (native /api/chat).

Uses Ollama's `format` field for JSON-schema constrained output and its native `tools` field for
function calling. Token usage comes from `prompt_eval_count` / `eval_count`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from swe_agent.core.errors import ProviderError
from swe_agent.llm.http import post_json
from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolCall, ToolSchema, Usage


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 300.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _convert(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ]
            if m.role == "tool" and m.name:
                item["tool_name"] = m.name
            out.append(item)
        return out

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str,
        params: GenParams,
        tools: list[ToolSchema] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {
            "temperature": params.temperature,
            "num_predict": params.max_tokens,
        }
        if params.seed is not None:
            options["seed"] = params.seed
        if params.num_ctx is not None:
            options["num_ctx"] = params.num_ctx
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert(messages),
            "stream": False,
            "options": options,
        }
        if params.think is not None:
            payload["think"] = params.think
        if json_schema is not None:
            payload["format"] = json_schema
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        t0 = time.perf_counter()
        body = await post_json(
            self._client, f"{self.base_url}/api/chat", payload, max_retries=self.max_retries
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        msg = body.get("message")
        if not isinstance(msg, dict):
            raise ProviderError("Ollama response missing 'message'", retryable=False)
        calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function", {})
            args = fn.get("arguments", {})
            calls.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=str(fn.get("name", "")),
                    arguments=args if isinstance(args, dict) else {"_raw": args},
                )
            )
        return LLMResponse(
            content=str(msg.get("content") or ""),
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=body.get("prompt_eval_count"),
                completion_tokens=body.get("eval_count"),
            ),
            latency_ms=latency_ms,
            model=str(body.get("model", model)),
            finish_reason=body.get("done_reason"),
            raw=body,
        )
