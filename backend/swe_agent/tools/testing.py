"""run_tests: the one execution tool, used only by the ReAct baseline.

The tool never touches Docker itself: the baseline supplies `ToolContext.test_runner`, which runs
pytest through the same hardened SandboxService the agent uses.
"""

from __future__ import annotations

from pydantic import Field

from swe_agent.core.errors import ToolPolicyError
from swe_agent.tools.base import ToolContext, ToolInput, ToolOutput, ToolSpec


class RunTestsIn(ToolInput):
    selection: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="pytest node ids or test files; empty = the whole test suite",
    )


class RunTestsOut(ToolOutput):
    summary: str

    def render(self) -> str:
        return self.summary


def run_tests(ctx: ToolContext, args: RunTestsIn) -> RunTestsOut:
    if ctx.test_runner is None:
        raise ToolPolicyError("running tests is not available in this stage")
    return RunTestsOut(summary=ctx.test_runner(list(args.selection)))


EXEC_TOOLS: list[ToolSpec] = [  # type: ignore[type-arg]
    ToolSpec(
        "run_tests",
        "Run the repository's tests in the sandbox (network off) and show the results.",
        RunTestsIn,
        "exec",
        run_tests,
        timeout_s=1500.0,
    ),
]
