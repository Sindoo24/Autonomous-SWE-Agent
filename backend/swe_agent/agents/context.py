"""Per-run context: the non-serializable services a run needs.

Graph state holds data; RunContext holds services (gateway, executor, workspace handle, symbol
index, recorder). It is passed to nodes via LangGraph's `config["configurable"]["ctx"]`, so it is
never written into checkpoints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from swe_agent.config import Settings
from swe_agent.core.budget import BudgetMeter
from swe_agent.llm.gateway import LLMGateway
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import Workspace
from swe_agent.sandbox.service import SandboxService
from swe_agent.tools.base import EditRecord
from swe_agent.tools.executor import ToolExecutor


@dataclass
class RunContext:
    settings: Settings
    task_id: str
    run_id: str
    artifacts: ArtifactStore
    recorder: TrajectoryRecorder
    meter: BudgetMeter
    gateway: LLMGateway
    executor: ToolExecutor
    workspace: Workspace | None = None
    index: SymbolIndex | None = None
    edits: list[EditRecord] = field(default_factory=list)
    sandbox: SandboxService | None = None  # None when execution is disabled
    # Cooperative cancellation, checked by the node wrapper before each node.
    cancel_requested: Callable[[], bool] = field(default=lambda: False)
    # OpenTelemetry span sink (closed by the runner when the run stops)
    span_sink: Any = None

    def require_workspace(self) -> tuple[Workspace, SymbolIndex]:
        if self.workspace is None or self.index is None:
            raise RuntimeError("workspace not prepared")
        return self.workspace, self.index


def get_ctx(config: Any) -> RunContext:
    ctx = config["configurable"]["ctx"]
    assert isinstance(ctx, RunContext)
    return ctx
