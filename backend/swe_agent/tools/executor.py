"""Tool executor.

`invoke` is the single entry point: lookup -> node permission -> budget -> argument validation ->
policy-checked handler (with timeout) -> truncation -> injection scan -> trajectory record.
Tool failures are returned as structured results, never raised into the graph.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from swe_agent.core.budget import BudgetMeter
from swe_agent.core.errors import PathPolicyError, RepoError, ToolPolicyError
from swe_agent.llm.types import ToolSchema
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import Stopwatch, TrajectoryRecorder
from swe_agent.prompts.safety import scan_for_injection
from swe_agent.repository.search import SearchError
from swe_agent.tools.base import ToolContext, ToolError, ToolResult, ToolSpec
from swe_agent.tools.registry import ALL_TOOLS, NODE_TOOLS

# Large enough for edit_file's grounding text (a small file or an outline), still bounded.
ERROR_MAX_CHARS = 3500

_EXPECTED_ERRORS = (PathPolicyError, ToolPolicyError, ValueError, SearchError, RepoError, OSError)


class ToolExecutor:
    def __init__(
        self,
        meter: BudgetMeter,
        recorder: TrajectoryRecorder,
        artifacts: ArtifactStore,
        *,
        max_output_chars: int = 12_000,
    ) -> None:
        self.meter = meter
        self.recorder = recorder
        self.artifacts = artifacts
        self.max_output_chars = max_output_chars
        self._seen: set[str] = set()
        # Calls that failed since the last successful write: repeating one cannot succeed.
        self._failed: set[str] = set()

    def reset_memory(self) -> None:
        """Forget repeat/failed-call history (after the workspace is rolled back)."""
        self._seen = set()
        self._failed = set()

    @staticmethod
    def schemas_for(node: str, extra: list[ToolSchema] | None = None) -> list[ToolSchema]:
        specs = [ALL_TOOLS[n] for n in NODE_TOOLS[node]]
        out = [
            ToolSchema(name=s.name, description=s.description, parameters=s.json_schema())
            for s in specs
        ]
        return out + (extra or [])

    async def invoke(self, name: str, raw_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        sw = Stopwatch()
        key = hashlib.sha256(
            json.dumps([name, raw_args], sort_keys=True, default=str).encode()
        ).hexdigest()
        repeated = key in self._seen
        self._seen.add(key)

        spec = ALL_TOOLS.get(name)
        result: ToolResult
        if spec is None or name not in NODE_TOOLS.get(ctx.node, []):
            result = self._error(name, "not_allowed", f"tool {name!r} is not available here", sw)
        else:
            self.meter.check()  # BudgetExceeded propagates: the node decides how to terminate
            self.meter.add_tool_call()
            result = await self._run(spec, raw_args, ctx, sw)
            if spec.effect == "write" and result.ok:
                # After a change, re-reading a file is legitimately new information, and a
                # previously failing call may now behave differently.
                self._seen = {key}
                self._failed = set()
        result.repeated = repeated
        repeated_failure = not result.ok and key in self._failed
        if not result.ok:
            self._failed.add(key)
        if repeated and result.ok:
            result.output = "(note: identical to an earlier call)\n" + result.output
        if repeated_failure:
            advice = (
                " read_file the target file and copy `search` verbatim from its current text."
                if name == "edit_file"
                else ""
            )
            result.output = (
                "REPEATED FAILED CALL: this exact call (same tool, same arguments) already "
                "failed and nothing has changed since, so repeating it cannot succeed. Take a "
                f"different action.{advice}\n" + result.output
            )

        flags = scan_for_injection(result.output) if result.ok else []
        full_ref = self.artifacts.put_text("tool_output", result.output)
        self.recorder.record(
            "tool_call",
            tool=name,
            args=raw_args,
            status="ok" if result.ok else "error",
            error_type=result.error.type if result.error else None,
            error=result.error.message if result.error else None,
            duration_ms=result.duration_ms,
            output_chars=len(result.output),
            truncated=result.truncated,
            repeated=repeated,
            repeated_failure=repeated_failure,
            output_artifact=full_ref.id,
        )
        if flags:
            self.recorder.record("injection_flag", tool=name, patterns=flags)
        return result

    async def _run(
        self,
        spec: ToolSpec,  # type: ignore[type-arg]
        raw_args: dict[str, Any],
        ctx: ToolContext,
        sw: Stopwatch,
    ) -> ToolResult:
        try:
            args = spec.input_model.model_validate(raw_args)
        except ValidationError as exc:
            msg = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'args'}: {e['msg']}" for e in exc.errors()
            )
            return self._error(spec.name, "invalid_arguments", msg[:1000], sw)
        try:
            out = await asyncio.wait_for(
                asyncio.to_thread(spec.handler, ctx, args), timeout=spec.timeout_s
            )
        except TimeoutError:
            return self._error(spec.name, "timeout", f"timed out after {spec.timeout_s}s", sw)
        except _EXPECTED_ERRORS as exc:
            kind = (
                "policy_violation"
                if isinstance(exc, PathPolicyError | ToolPolicyError)
                else ("tool_error")
            )
            return self._error(spec.name, kind, str(exc)[:ERROR_MAX_CHARS], sw)
        text = out.render()
        truncated = len(text) > self.max_output_chars
        if truncated:
            text = text[: self.max_output_chars] + "\n[... output truncated]"
        return ToolResult(
            tool=spec.name,
            ok=True,
            output=text,
            data=out.model_dump(),
            truncated=truncated,
            duration_ms=sw.ms,
        )

    @staticmethod
    def _error(name: str, kind: str, message: str, sw: Stopwatch) -> ToolResult:
        return ToolResult(
            tool=name,
            ok=False,
            output=f"ERROR ({kind}): {message}",
            error=ToolError(type=kind, message=message),
            duration_ms=sw.ms,
        )
