"""OpenAI-compatible provider: vLLM, llama.cpp server, LM Studio, or any hosted compatible API.

Structured output uses `response_format={"type": "json_schema", ...}` (supported by vLLM's
guided decoding and llama.cpp server). Native tools use the standard `tools` field.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from swe_agent.core.errors import ProviderError
from swe_agent.llm.http import post_json
from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolCall, ToolSchema, Usage


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 300.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Accept both "http://host:8000" and "http://host:8000/v1".
        base = base_url.rstrip("/")
        self.base_url = base if base.endswith("/v1") else f"{base}/v1"
        self.api_key = api_key
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
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            if m.role == "tool":
                item["tool_call_id"] = m.tool_call_id or ""
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert(messages),
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
        }
        if params.seed is not None:
            payload["seed"] = params.seed
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema},
            }
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
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None

        t0 = time.perf_counter()
        body = await post_json(
            self._client,
            f"{self.base_url}/chat/completions",
            payload,
            headers=headers,
            max_retries=self.max_retries,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("response has no choices", retryable=False)
        choice = choices[0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                    name=str(fn.get("name", "")),
                    arguments=args if isinstance(args, dict) else {"_raw": args},
                )
            )
        usage = body.get("usage") or {}
        return LLMResponse(
            content=str(msg.get("content") or ""),
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            ),
            latency_ms=latency_ms,
            model=str(body.get("model", model)),
            finish_reason=choice.get("finish_reason"),
            raw=body,
        )
