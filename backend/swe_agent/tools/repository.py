"""Read-only repository tools: list, read, search, symbols, references, outline."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from pydantic import Field

from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.search import ripgrep
from swe_agent.tools.base import ToolContext, ToolInput, ToolOutput, ToolSpec

MAX_FILE_BYTES = 2_000_000


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


# --------------------------------------------------------------------------- list_files


class ListFilesIn(ToolInput):
    path: str = Field(".", description="Directory relative to the repository root")
    depth: int = Field(2, ge=1, le=6, description="How many directory levels to expand")
    glob: str | None = Field(None, description="Optional filename filter, e.g. '*.py'")


class ListFilesOut(ToolOutput):
    root: str
    entries: list[str]
    total_files: int
    truncated: bool

    def render(self) -> str:
        head = f"{self.total_files} files under {self.root}"
        tail = "\n(truncated: narrow `path` or reduce `depth`)" if self.truncated else ""
        return head + "\n" + "\n".join(self.entries) + tail


def list_files(ctx: ToolContext, a: ListFilesIn) -> ListFilesOut:
    base = ctx.workspace.jail.resolve(a.path, must_exist=True)
    base_rel = ctx.workspace.jail.relative(base)
    if not base.is_dir():
        raise PathPolicyError(f"not a directory: {a.path!r}")
    prefix = "" if base_rel == "." else base_rel + "/"
    files = [f for f in ctx.workspace.ls_files() if f.startswith(prefix)]
    if a.glob:
        files = [f for f in files if PurePosixPath(f).match(a.glob)]
    collapsed: dict[str, int] = defaultdict(int)
    entries: list[str] = []
    for f in files:
        rest = PurePosixPath(f[len(prefix) :])
        if len(rest.parts) > a.depth:
            collapsed[prefix + "/".join(rest.parts[: a.depth]) + "/"] += 1
        else:
            entries.append(f)
    entries += [f"{d} ({n} files)" for d, n in collapsed.items()]
    entries.sort()
    limit = 300
    return ListFilesOut(
        root=base_rel,
        entries=entries[:limit],
        total_files=len(files),
        truncated=len(entries) > limit,
    )


# --------------------------------------------------------------------------- read_file


class ReadFileIn(ToolInput):
    path: str = Field(description="File path relative to the repository root")
    start_line: int = Field(1, ge=1)
    end_line: int | None = Field(None, ge=1, description="Inclusive; defaults to start_line+max-1")


class ReadFileOut(ToolOutput):
    path: str
    start_line: int
    end_line: int
    total_lines: int
    text: str

    def render(self) -> str:
        more = (
            f"\n[... {self.total_lines - self.end_line} more lines; "
            f"call read_file with start_line={self.end_line + 1}]"
            if self.end_line < self.total_lines
            else ""
        )
        return (
            f"{self.path} (lines {self.start_line}-{self.end_line} of {self.total_lines})\n"
            f"{self.text}{more}"
        )


def read_file(ctx: ToolContext, a: ReadFileIn) -> ReadFileOut:
    p = ctx.workspace.jail.resolve(a.path, must_exist=True)
    if not p.is_file():
        raise PathPolicyError(f"not a file: {a.path!r}")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file too large to read ({p.stat().st_size} bytes)")
    data = p.read_bytes()
    if _is_binary(data):
        raise ValueError("binary file")
    lines = data.decode("utf-8", errors="replace").splitlines()
    total = len(lines)
    start = min(a.start_line, max(total, 1))
    end = a.end_line or start + ctx.max_read_lines - 1
    end = min(end, total, start + ctx.max_read_lines - 1)
    width = len(str(max(end, 1)))
    text = "\n".join(f"{i:>{width}}| {lines[i - 1]}" for i in range(start, end + 1))
    return ReadFileOut(
        path=ctx.workspace.jail.relative(p),
        start_line=start,
        end_line=max(end, start - 1),
        total_lines=total,
        text=text,
    )


# --------------------------------------------------------------------------- search_code


class SearchCodeIn(ToolInput):
    pattern: str = Field(min_length=1, max_length=300, description="Regex (or literal text)")
    path: str = Field(".", description="Directory or file to search")
    glob: str | None = Field(None, description="e.g. '*.py'")
    fixed_string: bool = Field(False, description="Treat pattern as literal text")
    ignore_case: bool = False
    context: int = Field(1, ge=0, le=5)
    max_results: int = Field(30, ge=1, le=100)


class SearchCodeOut(ToolOutput):
    pattern: str
    hits: list[str]
    files_with_matches: int
    truncated: bool

    def render(self) -> str:
        if not self.hits:
            return f"no matches for {self.pattern!r}"
        more = "\n(more matches omitted: refine the pattern or path)" if self.truncated else ""
        return (
            f"{len(self.hits)} matches in {self.files_with_matches} files for {self.pattern!r}\n"
            + "\n".join(self.hits)
            + more
        )


def search_code(ctx: ToolContext, a: SearchCodeIn) -> SearchCodeOut:
    target = ctx.workspace.jail.resolve(a.path, must_exist=True)
    rel = ctx.workspace.jail.relative(target)
    res = ripgrep(
        ctx.workspace.root,
        a.pattern,
        search_path=rel,
        glob=a.glob,
        fixed_string=a.fixed_string,
        ignore_case=a.ignore_case,
        context=a.context,
        max_results=a.max_results,
    )
    blocks: list[str] = []
    for h in res.hits:
        lines = [
            f"{h.path}:{h.line - len(h.context_before) + i}- {t}"
            for i, t in enumerate(h.context_before)
        ]
        lines.append(f"{h.path}:{h.line}: {h.text}")
        lines += [f"{h.path}:{h.line + 1 + i}- {t}" for i, t in enumerate(h.context_after)]
        blocks.append("\n".join(lines))
    return SearchCodeOut(
        pattern=a.pattern,
        hits=blocks,
        files_with_matches=res.files_with_matches,
        truncated=res.truncated,
    )


# --------------------------------------------------------------------------- find_symbol


class FindSymbolIn(ToolInput):
    name: str = Field(min_length=1, max_length=200, description="Name or dotted qualname")
    kind: str | None = Field(None, description="Optional: function | method | class")


class SymbolsOut(ToolOutput):
    query: str
    results: list[str]

    def render(self) -> str:
        if not self.results:
            return f"no definitions found for {self.query!r}"
        return f"{len(self.results)} definition(s) of {self.query!r}\n" + "\n".join(self.results)


def find_symbol(ctx: ToolContext, a: FindSymbolIn) -> SymbolsOut:
    defs = ctx.index.find(a.name, a.kind)[:30]
    return SymbolsOut(
        query=a.name,
        results=[
            f"{d.path}:{d.start_line}-{d.end_line} {d.kind} {d.qualname}: {d.signature}"
            for d in defs
        ],
    )


# --------------------------------------------------------------------------- find_references


class FindReferencesIn(ToolInput):
    name: str = Field(min_length=1, max_length=120, description="Identifier to look up")
    max_results: int = Field(40, ge=1, le=100)


class ReferencesOut(ToolOutput):
    name: str
    results: list[str]
    truncated: bool

    def render(self) -> str:
        if not self.results:
            return f"no references to {self.name!r}"
        note = " (name-based, not type-resolved)"
        more = "\n(truncated)" if self.truncated else ""
        return (
            f"{len(self.results)} reference(s) to {self.name!r}{note}\n"
            + "\n".join(self.results)
            + more
        )


def find_references(ctx: ToolContext, a: FindReferencesIn) -> ReferencesOut:
    simple = a.name.split(".")[-1]
    res = ripgrep(
        ctx.workspace.root,
        simple,
        fixed_string=True,
        word=True,
        glob="*.py",
        max_results=a.max_results,
    )
    def_lines = {(d.path, d.start_line) for d in ctx.index.find(simple)}
    out = []
    for h in res.hits:
        enclosing = ctx.index.enclosing(h.path, h.line)
        tag = "[definition] " if (h.path, h.line) in def_lines else ""
        where = f" (in {enclosing.qualname})" if enclosing else ""
        out.append(f"{h.path}:{h.line}{where}: {tag}{h.text.strip()}")
    return ReferencesOut(name=a.name, results=out, truncated=res.truncated)


# --------------------------------------------------------------------------- outline_file


class OutlineFileIn(ToolInput):
    path: str


class OutlineOut(ToolOutput):
    path: str
    total_lines: int
    imports: list[str]
    symbols: list[str]
    parse_errors: bool

    def render(self) -> str:
        parts = [f"{self.path} ({self.total_lines} lines)"]
        if self.parse_errors:
            parts.append("(file contains syntax errors; outline may be partial)")
        if self.imports:
            parts.append("imports:\n" + "\n".join(f"  {i}" for i in self.imports))
        parts.append("symbols:\n" + ("\n".join(self.symbols) if self.symbols else "  (none)"))
        return "\n".join(parts)


def outline_file(ctx: ToolContext, a: OutlineFileIn) -> OutlineOut:
    p = ctx.workspace.jail.resolve(a.path, must_exist=True)
    rel = ctx.workspace.jail.relative(p)
    if not rel.endswith(".py"):
        raise ValueError("outline_file supports Python files only")
    ctx.index.update_file(ctx.workspace.root, rel)
    fi = ctx.index.outline(rel)
    if fi is None:
        raise ValueError(f"could not index {rel}")
    syms = []
    for s in fi.symbols:
        indent = "  " * (s.qualname.count(".") + 1)
        syms.append(f"{indent}L{s.start_line}-{s.end_line} {s.kind} {s.signature}")
    return OutlineOut(
        path=rel,
        total_lines=fi.line_count,
        imports=[f"L{i.line} {i.text}" for i in fi.imports][:40],
        symbols=syms[:200],
        parse_errors=fi.has_errors,
    )


REPO_TOOLS: list[ToolSpec] = [  # type: ignore[type-arg]
    ToolSpec(
        "list_files",
        "List repository files (respects .gitignore).",
        ListFilesIn,
        "read",
        list_files,
    ),
    ToolSpec(
        "read_file",
        "Read a line range of a file (max 300 lines per call), with line numbers.",
        ReadFileIn,
        "read",
        read_file,
    ),
    ToolSpec(
        "search_code",
        "Search file contents with ripgrep (regex by default).",
        SearchCodeIn,
        "read",
        search_code,
    ),
    ToolSpec(
        "find_symbol",
        "Find where a function/class/method is defined.",
        FindSymbolIn,
        "read",
        find_symbol,
    ),
    ToolSpec(
        "find_references",
        "Find lines where an identifier is used (name-based).",
        FindReferencesIn,
        "read",
        find_references,
    ),
    ToolSpec(
        "outline_file",
        "Show a Python file's imports, classes and functions with line "
        "ranges. Cheaper than reading the whole file.",
        OutlineFileIn,
        "read",
        outline_file,
    ),
]
