"""Tool registry: every tool the system knows, and which tools each agent stage may call.

Explore can never write; implement cannot run anything; only the ReAct baseline gets `run_tests`.
"""

from __future__ import annotations

from swe_agent.tools.base import ToolSpec
from swe_agent.tools.filesystem import EDIT_TOOLS
from swe_agent.tools.git import GIT_READ_TOOLS, GIT_WORKTREE_TOOLS
from swe_agent.tools.repository import REPO_TOOLS
from swe_agent.tools.testing import EXEC_TOOLS

ALL_TOOLS: dict[str, ToolSpec] = {  # type: ignore[type-arg]
    t.name: t for t in [*REPO_TOOLS, *GIT_READ_TOOLS, *GIT_WORKTREE_TOOLS, *EDIT_TOOLS, *EXEC_TOOLS]
}

_READ = [t.name for t in REPO_TOOLS]
# Which tools each node may use. Explore can never write; implement cannot run anything.
NODE_TOOLS: dict[str, list[str]] = {
    "explore": [*_READ, "git_log", "git_show"],
    "implement": [*_READ, "git_diff", "git_status", "edit_file", "create_file"],
    # Baseline B (ReAct): one loop with every tool, including running tests.
    "react": [
        *_READ,
        "git_log",
        "git_show",
        "git_diff",
        "git_status",
        "edit_file",
        "create_file",
        "run_tests",
    ],
}
