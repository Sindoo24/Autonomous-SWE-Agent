"""Provider adapters against mocked HTTP endpoints that follow each server's documented schema."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from swe_agent.config import ModelSettings, ProviderKind
from swe_agent.core.errors import ProviderError
from swe_agent.llm.factory import build_provider
from swe_agent.llm.ollama import OllamaProvider
from swe_agent.llm.openai_compatible import OpenAICompatibleProvider
from swe_agent.llm.types import GenParams, Message, ToolCall, ToolSchema
from tests.conftest import REPO_ROOT

TOOLS = [ToolSchema(name="read_file", description="read", parameters={"type": "object"})]
PARAMS = GenParams(temperature=0.0, max_tokens=256, seed=7, num_ctx=8192, think=False)


@respx.mock
async def test_ollama_request_and_response_shape() -> None:
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
                    ],
                },
                "done_reason": "stop",
                "prompt_eval_count": 120,
                "eval_count": 30,
            },
        )
    )
    p = OllamaProvider("http://ollama:11434/", max_retries=0)
    msgs = [
        Message(role="system", content="sys"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="x", arguments={"a": 1})],
        ),
        Message(role="tool", content="out", tool_call_id="1", name="x"),
    ]
    resp = await p.generate(
        msgs, model="qwen3:8b", params=PARAMS, tools=TOOLS, json_schema={"type": "object"}
    )
    body = json.loads(route.calls.last.request.content)
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "num_predict": 256, "seed": 7, "num_ctx": 8192}
    assert body["format"] == {"type": "object"}
    assert body["think"] is False
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}
    assert body["messages"][2]["tool_name"] == "x"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "a.py"}
    assert resp.usage.prompt_tokens == 120 and resp.usage.total == 150
    await p.aclose()


@respx.mock
async def test_openai_compatible_request_and_response_shape() -> None:
    route = respx.post("http://vllm:8000/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "qwen-coder",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "b.py"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    p = OpenAICompatibleProvider("http://vllm:8000", api_key="k", max_retries=0)
    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="x", arguments={"a": 1})],
        ),
        Message(role="tool", content="out", tool_call_id="c1", name="x"),
    ]
    resp = await p.generate(
        msgs, model="qwen-coder", params=PARAMS, tools=TOOLS, json_schema={"type": "object"}
    )
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["Authorization"] == "Bearer k"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert body["seed"] == 7 and body["max_tokens"] == 256
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert body["messages"][2]["tool_call_id"] == "c1"
    assert resp.content == ""
    assert resp.tool_calls[0].id == "call_9" and resp.tool_calls[0].arguments == {"path": "b.py"}
    assert resp.usage.total == 15 and resp.finish_reason == "tool_calls"
    await p.aclose()


@respx.mock
async def test_missing_usage_is_none_not_estimated() -> None:
    respx.post("http://vllm:8000/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    p = OpenAICompatibleProvider("http://vllm:8000/v1", max_retries=0)
    resp = await p.generate([Message(role="user", content="x")], model="m", params=PARAMS)
    assert resp.usage.prompt_tokens is None and resp.usage.total is None


@respx.mock
async def test_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swe_agent.llm.http.asyncio.sleep", _no_sleep)
    route = respx.post("http://ollama:11434/api/chat").mock(
        side_effect=[
            httpx.Response(503, text="loading model"),
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"message": {"content": "ok"}}),
        ]
    )
    p = OllamaProvider("http://ollama:11434", max_retries=2)
    resp = await p.generate([Message(role="user", content="x")], model="m", params=PARAMS)
    assert resp.content == "ok" and route.call_count == 3


@respx.mock
async def test_non_retryable_error_raises_immediately() -> None:
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    p = OllamaProvider("http://ollama:11434", max_retries=3)
    with pytest.raises(ProviderError, match="HTTP 400"):
        await p.generate([Message(role="user", content="x")], model="m", params=PARAMS)
    assert route.call_count == 1


def test_factory_selects_provider() -> None:
    assert isinstance(build_provider(ModelSettings(provider=ProviderKind.OLLAMA)), OllamaProvider)
    p = build_provider(
        ModelSettings(provider=ProviderKind.OPENAI_COMPATIBLE, base_url="http://x:8000")
    )
    assert isinstance(p, OpenAICompatibleProvider) and p.base_url == "http://x:8000/v1"


async def _no_sleep(_: float) -> None:
    return None


@respx.mock
async def test_think_none_omits_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Models without a thinking mode: MODEL_THINK=none must not send `think` at all."""
    monkeypatch.setenv("MODEL_THINK", "none")
    cfg = ModelSettings()
    assert cfg.think is None
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "{}"}})
    )
    p = OllamaProvider("http://ollama:11434", max_retries=0)
    from swe_agent.llm.factory import gen_params

    await p.generate([Message(role="user", content="x")], model="m", params=gen_params(cfg))
    assert "think" not in json.loads(route.calls.last.request.content)


def test_small_profile_loads() -> None:

    from swe_agent.config import load_settings

    s = load_settings(REPO_ROOT / "configs/models/ollama-small.env")
    assert s.model.reasoning == s.model.coder == "qwen2.5-coder:7b"
    assert s.model.think is None and s.model.num_ctx == 16384
    assert s.tool_output_max_chars * 6 <= s.loop_context_max_chars + 1  # fits the window
