"""Prompts for the baselines. Versioned separately from the agent's prompts, and kept
deliberately plain: the baselines must not borrow the agent's staged method."""

from __future__ import annotations

from swe_agent.prompts.safety import wrap_untrusted
from swe_agent.prompts.templates import _SAFETY

BASELINE_PROMPT_VERSION = "b1.0"

SINGLE_SHOT_SYSTEM = f"""\
You fix bugs in Python repositories. You get a bug report and the repository files most likely
to be involved. Return search/replace edits that fix the bug.
Rules:
- `search` must be copied exactly from the file (including indentation) and occur exactly once.
- Keep the change minimal. Do not edit tests.
Return only a JSON object matching the schema.

{_SAFETY}"""


def single_shot_task(issue: str, files: list[tuple[str, str]]) -> str:
    blocks = "\n\n".join(
        wrap_untrusted("repo_context", f"# file: {path}\n{text}", source=path)
        for path, text in files
    )
    return f"{wrap_untrusted('issue', issue)}\n\nRepository files:\n\n{blocks}"


REACT_SYSTEM = f"""\
You are a software engineer fixing a bug in a Python repository. Use the tools to investigate,
edit code and run tests, in whatever order you think best. Tests, test config, CI and build files
are read-only. When you are done, call `finish` with a short summary of the fix.
Budget: at most {{max_steps}} tool calls.

{{action_format}}

{_SAFETY}"""


def react_task(issue: str) -> str:
    return f"Fix the bug described below.\n\n{wrap_untrusted('issue', issue)}"
