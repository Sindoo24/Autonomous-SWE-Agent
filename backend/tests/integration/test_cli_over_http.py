"""CLI -> settings -> real HTTP provider -> graph, against a local stub server.

The stub speaks the OpenAI-compatible and Ollama wire formats and replays scripted responses.
This exercises configuration-driven provider selection and real HTTP round-trips; it says
nothing about model quality.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from swe_agent.cli import main
from tests.integration.test_graph_e2e import (
    HYPOTHESES,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)


class _Stub:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, dict[str, Any]]] = []


def _handler(stub: _Stub) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            stub.requests.append((self.path, body))
            content = json.dumps(stub.script.pop(0))
            if self.path == "/v1/chat/completions":
                out = {
                    "model": body["model"],
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            elif self.path == "/api/chat":
                out = {
                    "model": body["model"],
                    "message": {"role": "assistant", "content": content},
                    "done_reason": "stop",
                    "prompt_eval_count": 100,
                    "eval_count": 20,
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return H


@pytest.fixture
def stub_server() -> Iterator[tuple[str, _Stub]]:
    script = [*explore_script(), HYPOTHESES, PLAN, *implement_script()]
    stub = _Stub(script)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(stub))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}", stub
    server.shutdown()


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
def test_cli_end_to_end_over_http(
    provider: str,
    stub_server: tuple[str, _Stub],
    users_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, stub = stub_server
    env = tmp_path / "profile.env"
    env.write_text(
        f"MODEL_PROVIDER={provider}\nMODEL_BASE_URL={url}\nMODEL_REASONING=reasoner-x\n"
        f"MODEL_CODER=coder-y\nSWE_WORKSPACE_ROOT={tmp_path / 'ws'}\n"
        f"SWE_ARTIFACTS_ROOT={tmp_path / 'art'}\nSANDBOX_ENABLED=false\n"
        f"AGENT_APPROVAL=auto\nSWE_CHECKPOINT_DB={tmp_path / 'cp.sqlite'}\n"
    )
    for key in ("MODEL_PROVIDER", "MODEL_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    code = main(["run", "--repo", str(users_repo), "--issue", ISSUE, "--env-file", str(env)])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "NOT VERIFIED (level 0)  termination: patch_proposed" in out
    assert "files changed: users_service/api.py" in out
    # 9 model calls x (100 in, 20 out) reported by the server
    assert "tokens in/out: 900/180" in out
    expected_path = "/v1/chat/completions" if provider == "openai_compatible" else "/api/chat"
    assert {p for p, _ in stub.requests} == {expected_path}
    models = [b["model"] for _, b in stub.requests]
    assert models[:5] == ["reasoner-x"] * 5 and models[5:] == ["coder-y"] * 4
    first = stub.requests[0][1]
    if provider == "openai_compatible":
        assert first["response_format"]["type"] == "json_schema"
    else:
        assert "anyOf" in first["format"] and first["think"] is False
