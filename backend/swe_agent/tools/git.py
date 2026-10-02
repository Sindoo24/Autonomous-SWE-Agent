"""Read-only git tools. All go through the hardened `run_git`; revisions are validated."""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import Field

from swe_agent.repository.workspace import validate_rev
from swe_agent.tools.base import ToolContext, ToolInput, ToolOutput, ToolSpec

_MAX_OUT = 20_000


class TextOut(ToolOutput):
    text: str

    def render(self) -> str:
        return self.text or "(empty)"


def _safe_rel(ctx: ToolContext, path: str) -> str:
    # Validates shape and jail; the file may have been deleted since, so no existence check.
    ctx.workspace.jail.resolve(path)
    return PurePosixPath(path.strip()).as_posix()


class GitLogIn(ToolInput):
    path: str | None = Field(None, description="Limit history to this file or directory")
    max_count: int = Field(10, ge=1, le=50)


def git_log(ctx: ToolContext, a: GitLogIn) -> TextOut:
    args = ["log", "--no-color", "--date=short", "--format=%h %ad %an: %s", f"-n{a.max_count}"]
    if a.path:
        args += ["--", _safe_rel(ctx, a.path)]
    return TextOut(text=ctx.workspace.git(*args))


class GitShowIn(ToolInput):
    rev: str = Field(description="Commit hash or ref, e.g. 'a1b2c3d' or 'HEAD~1'")
    path: str | None = Field(None, description="Limit the shown diff to this path")


def git_show(ctx: ToolContext, a: GitShowIn) -> TextOut:
    rev = validate_rev(a.rev)
    args = [
        "show",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--stat",
        "--patch",
        "--format=commit %H%nAuthor: %an%nDate: %ad%n%n%s%n%n%b",
        "--end-of-options",
        rev,
    ]
    if a.path:
        args += ["--", _safe_rel(ctx, a.path)]
    out = ctx.workspace.git(*args)
    if len(out) > _MAX_OUT:
        out = out[:_MAX_OUT] + "\n[... diff truncated; pass `path` to narrow]"
    return TextOut(text=out)


class NoArgs(ToolInput):
    pass


def git_diff(ctx: ToolContext, a: NoArgs) -> TextOut:
    return TextOut(text=ctx.workspace.diff() or "(no changes yet)")


def git_status(ctx: ToolContext, a: NoArgs) -> TextOut:
    ctx.workspace.stage_all()
    out = ctx.workspace.git(
        "diff", "--cached", "--name-status", "--no-ext-diff", ctx.workspace.base_commit, "--"
    )
    return TextOut(text=out or "(no changes since base commit)")


GIT_READ_TOOLS: list[ToolSpec] = [  # type: ignore[type-arg]
    ToolSpec("git_log", "Recent commits, optionally for one path.", GitLogIn, "read", git_log),
    ToolSpec("git_show", "Show one commit's message and diff.", GitShowIn, "read", git_show),
]

GIT_WORKTREE_TOOLS: list[ToolSpec] = [  # type: ignore[type-arg]
    ToolSpec("git_diff", "Show your current changes as a unified diff.", NoArgs, "read", git_diff),
    ToolSpec("git_status", "List files you have changed.", NoArgs, "read", git_status),
]
