"""ripgrep wrapper. argv only; the pattern is passed with `-e` so it can never be read as a flag."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

RG = shutil.which("rg")


@dataclass
class SearchHit:
    path: str
    line: int
    text: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[SearchHit]
    truncated: bool
    files_with_matches: int


class SearchError(Exception):
    pass


def ripgrep(
    root: Path,
    pattern: str,
    *,
    search_path: str = ".",
    glob: str | None = None,
    fixed_string: bool = False,
    word: bool = False,
    ignore_case: bool = False,
    context: int = 0,
    max_results: int = 50,
    timeout: float = 20.0,
) -> SearchResult:
    if RG is None:
        raise SearchError("ripgrep (rg) is not installed")
    argv = [
        RG,
        "--json",
        "--no-config",
        "--max-filesize",
        "1M",
        "--max-columns",
        "400",
        "--max-columns-preview",
        "--no-follow",
        "-g",
        "!.git",
    ]
    if fixed_string:
        argv.append("-F")
    if word:
        argv.append("-w")
    if ignore_case:
        argv.append("-i")
    if context:
        argv += ["-C", str(context)]
    if glob:
        argv += ["-g", glob]
    argv += ["-e", pattern, "--", search_path]

    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise SearchError(f"search timed out after {timeout}s") from exc
    if proc.returncode == 2:
        raise SearchError(proc.stderr.strip()[:500] or "ripgrep error")

    hits: list[SearchHit] = []
    files: set[str] = set()
    pending_before: list[str] = []
    truncated = False
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = ev.get("type")
        data = ev.get("data", {})
        if typ == "begin":
            pending_before = []
            continue
        if typ not in ("match", "context"):
            continue
        path = data.get("path", {}).get("text", "")
        path = path[2:] if path.startswith("./") else path
        text = (data.get("lines", {}).get("text") or "").rstrip("\n")
        lineno = int(data.get("line_number") or 0)
        if typ == "context":
            last = hits[-1] if hits else None
            if (
                last is not None
                and last.path == path
                and lineno > last.line
                and len(last.context_after) < context
            ):
                last.context_after.append(text)
                continue
            pending_before = [*pending_before, text][-context:] if context else []
            continue
        files.add(path)
        if len(hits) >= max_results:
            truncated = True
            continue
        hits.append(SearchHit(path=path, line=lineno, text=text, context_before=pending_before))
        pending_before = []
    return SearchResult(hits=hits, truncated=truncated, files_with_matches=len(files))
