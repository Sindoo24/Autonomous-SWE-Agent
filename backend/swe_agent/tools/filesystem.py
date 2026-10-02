"""Write tools: exact search/replace edits and new-file creation.

Why search/replace and not unified diffs from the model: small open models frequently emit
malformed hunks (wrong line numbers/headers). An exact-match block is easy for the model to
produce, trivially validated, and fails loudly with a useful hint when it does not match.
"""

from __future__ import annotations

import ast
import difflib

from pydantic import Field

from swe_agent.tools.base import EditRecord, ToolContext, ToolInput, ToolOutput, ToolSpec

MAX_NEW_FILE_BYTES = 100_000


class EditOut(ToolOutput):
    path: str
    snippet: str
    note: str = ""
    syntax_warning: str = ""

    def render(self) -> str:
        parts = [f"edited {self.path}"]
        if self.note:
            parts.append(f"note: {self.note}")
        if self.syntax_warning:
            parts.append(f"WARNING: {self.syntax_warning}")
        parts.append(self.snippet)
        return "\n".join(parts)


def _syntax_warning(path: str, text: str) -> str:
    if not path.endswith(".py"):
        return ""
    try:
        ast.parse(text, filename=path)
    except SyntaxError as exc:
        return f"file now has a syntax error at line {exc.lineno}: {exc.msg}"
    return ""


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


NO_MATCH_MAX_LINES = 60
NO_MATCH_MAX_CHARS = 2500
_GROUNDING_RULE = (
    "`search` must be copied verbatim from the file as it is now (never from the plan or from "
    "memory). New code goes only in `replace`."
)


def _closest_hint(content: str, search: str) -> str | None:
    """Near-miss hint: a numbered window around the most similar line, or None if nothing in the
    file resembles the first line of `search`."""
    lines = content.splitlines()
    first = next((ln for ln in search.splitlines() if ln.strip()), "")
    if not first:
        return None
    candidates = difflib.get_close_matches(first, lines, n=1, cutoff=0.5)
    if not candidates:
        stripped = first.strip()
        candidates = [ln for ln in lines if stripped and stripped in ln][:1]
    if not candidates:
        return None
    idx = lines.index(candidates[0])
    lo, hi = max(0, idx - 3), min(len(lines), idx + 4)
    window = "\n".join(f"{i + 1}| {lines[i]}" for i in range(lo, hi))
    return f" Closest match near line {idx + 1} (check exact whitespace/indentation):\n{window}"


def _grounding(ctx: ToolContext, rel: str, content: str) -> str:
    """Nothing in the file resembles `search`: the model is editing from its plan or memory.
    Show the real text (small files, unnumbered so it can be copied verbatim) or an outline with
    line ranges (large files) so the next attempt can be grounded."""
    lines = content.splitlines()
    head = f" Nothing in {rel} resembles the first line of `search`. {_GROUNDING_RULE}"
    if len(lines) <= NO_MATCH_MAX_LINES and len(content) <= NO_MATCH_MAX_CHARS:
        return (
            f"{head}\nCurrent content of {rel} ({len(lines)} lines):\n"
            f"----- begin {rel} -----\n{content.rstrip()}\n----- end {rel} -----"
        )
    fi = ctx.index.outline(rel)
    outline = (
        "\n".join(
            f"  L{sd.start_line}-{sd.end_line} {sd.kind} {sd.qualname}" for sd in fi.symbols[:40]
        )
        if fi and fi.symbols
        else "  (no functions or classes found)"
    )
    return (
        f"{head}\n{rel} has {len(lines)} lines; its definitions:\n{outline}\n"
        "Call read_file with start_line/end_line for the part you need, then copy `search` "
        "from it."
    )


def _snippet(before: str, after: str, path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", n=2, lineterm=""
    )
    lines = list(diff)
    if len(lines) > 60:
        lines = [*lines[:60], "[... snippet truncated; use git_diff]"]
    return "\n".join(lines)


class EditFileIn(ToolInput):
    path: str
    search: str = Field(
        description="Exact existing text to replace, including indentation. "
        "Include enough surrounding lines to be unique."
    )
    replace: str = Field(description="Replacement text")


def edit_file(ctx: ToolContext, a: EditFileIn) -> EditOut:
    p = ctx.workspace.jail.resolve(a.path, must_exist=True)
    rel = ctx.workspace.jail.relative(p)
    ctx.write_policy.check(rel, exists=True)
    if not p.is_file():
        raise ValueError(f"not a file: {rel}")
    if not a.search:
        raise ValueError("`search` must not be empty (use create_file for new files)")
    if a.search == a.replace:
        raise ValueError("`search` and `replace` are identical; nothing to do")
    content = p.read_text(encoding="utf-8")
    note = ""
    count = content.count(a.search)
    if count > 1:
        starts, pos = [], content.find(a.search)
        while pos != -1:
            starts.append(_line_of(content, pos))
            pos = content.find(a.search, pos + 1)
        raise ValueError(
            f"`search` matches {count} times (lines {starts[:10]}); include more surrounding "
            "context so it matches exactly once"
        )
    if count == 1:
        new = content.replace(a.search, a.replace, 1)
    else:
        # Single fallback: tolerate trailing-whitespace differences only (never indentation).
        norm_lines = [ln.rstrip() for ln in content.splitlines()]
        search_lines = [ln.rstrip() for ln in a.search.splitlines()]
        n = len(search_lines)
        matches = [
            i for i in range(len(norm_lines) - n + 1) if norm_lines[i : i + n] == search_lines
        ]
        if len(matches) != 1:
            hint = _closest_hint(content, a.search)
            raise ValueError(
                "`search` text not found in file."
                + (hint if hint is not None else _grounding(ctx, rel, content))
            )
        orig_lines = content.splitlines(keepends=True)
        i = matches[0]
        replacement = a.replace if a.replace.endswith("\n") or not a.replace else a.replace + "\n"
        tail_nl = orig_lines[i + n - 1].endswith("\n")
        if not tail_nl:
            replacement = replacement.rstrip("\n")
        new = "".join(orig_lines[:i]) + replacement + "".join(orig_lines[i + n :])
        note = "matched ignoring trailing whitespace"
    p.write_text(new, encoding="utf-8")
    ctx.index.update_file(ctx.workspace.root, rel)
    ctx.edits.append(
        EditRecord(
            tool="edit_file",
            path=rel,
            lines_before=content.count("\n"),
            lines_after=new.count("\n"),
            note=note,
        )
    )
    return EditOut(
        path=rel,
        snippet=_snippet(content, new, rel),
        note=note,
        syntax_warning=_syntax_warning(rel, new),
    )


class CreateFileIn(ToolInput):
    path: str
    content: str


def create_file(ctx: ToolContext, a: CreateFileIn) -> EditOut:
    p = ctx.workspace.jail.resolve(a.path)
    rel = ctx.workspace.jail.relative(p)
    if p.exists():
        raise ValueError(f"{rel} already exists; use edit_file")
    ctx.write_policy.check(rel, exists=False)
    if len(a.content.encode()) > MAX_NEW_FILE_BYTES:
        raise ValueError("file content too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after mkdir: a symlinked parent could only be followed if it pointed inside.
    ctx.workspace.jail.resolve(rel)
    p.write_text(a.content, encoding="utf-8")
    ctx.index.update_file(ctx.workspace.root, rel)
    ctx.edits.append(
        EditRecord(tool="create_file", path=rel, lines_before=0, lines_after=a.content.count("\n"))
    )
    return EditOut(
        path=rel,
        snippet=_snippet("", a.content, rel),
        syntax_warning=_syntax_warning(rel, a.content),
    )


EDIT_TOOLS: list[ToolSpec] = [  # type: ignore[type-arg]
    ToolSpec(
        "edit_file",
        "Replace one exact occurrence of `search` with `replace` in a file. "
        "Make small, targeted edits.",
        EditFileIn,
        "write",
        edit_file,
    ),
    ToolSpec(
        "create_file", "Create a new file (fails if it exists).", CreateFileIn, "write", create_file
    ),
]
