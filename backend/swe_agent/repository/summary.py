"""Layers 1, 2 and 5 of repository understanding: tree digest, file metadata, candidate ranking.

The candidate ranker is deliberately deterministic and cheap. It seeds the explorer with a short
list of files and the reason each is a candidate, which removes blind exploration calls. Whether
it actually helps is measured later as an ablation (explore with vs. without seeding).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal

from swe_agent.repository.pathjail import is_test_path
from swe_agent.repository.search import SearchError, ripgrep
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.schemas.state import CandidateFile, RepoSummary

_STOPWORDS = frozenset(
    " ".join(
        [
            "a an the and or but if then else when while with without for from into onto of",
            "on in at to by is are was were be been being do does did done has have had",
            "having not no yes this that these those it its it's there their them they we you",
            "your i me my our us should would could can will shall may might must also just",
            "only than too very more most less least some any all each every other same such",
            "own so as up down out over under again further once here why how what which who",
            "whom where returns return returned get gets got set sets use used uses using",
            "make makes made instead expected actual actually currently error errors bug bugs",
            "issue issues fix fixed broken wrong works work working fails fail failing failed",
            "crash crashes value values field fields function method class file files code",
            "line lines case cases data result results true false none null number string",
            "list dict object call calls called given raise raises raised http status",
        ]
    ).split()
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_QUOTED_RE = re.compile(r"`([^`]{2,80})`|\"([^\"]{2,80})\"|'([^']{2,80})'")
_URLPATH_RE = re.compile(r"(?<![\w.])/(?:[A-Za-z0-9_{}<>:-]+/?)+")
_ERROR_RE = re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b")


def _is_identifier_like(tok: str) -> bool:
    return (
        "_" in tok
        or "." in tok
        or any(c.isdigit() for c in tok)
        or (
            tok[:1].isupper()
            and any(c.islower() for c in tok[1:])
            and any(c.isupper() for c in tok[1:])
        )
    )


def extract_issue_terms(issue: str, max_terms: int = 16) -> tuple[list[str], set[str]]:
    """Return (terms ordered by priority, the subset that are 'strong' code-like identifiers)."""
    strong: list[str] = []
    weak: list[str] = []

    for m in _QUOTED_RE.finditer(issue):
        q = next(g for g in m.groups() if g)
        for ident in _IDENT_RE.findall(q):
            strong.append(ident)
    for m in _URLPATH_RE.finditer(issue):
        for seg in m.group(0).strip("/").split("/"):
            seg = seg.strip("{}<>:")
            if len(seg) >= 3 and not seg.isdigit():
                strong.append(seg)
    strong += _ERROR_RE.findall(issue)
    for tok in _IDENT_RE.findall(issue):
        low = tok.lower()
        if low in _STOPWORDS or len(tok) < 3:
            continue
        if _is_identifier_like(tok):
            strong.append(tok)
            if "." in tok:
                strong += tok.split(".")
        elif len(tok) >= 4:
            weak.append(low)

    ordered: list[str] = []
    for t in [*strong, *weak]:
        if t not in ordered and t.lower() not in _STOPWORDS:
            ordered.append(t)
    return ordered[:max_terms], {t for t in strong if t in ordered[:max_terms]}


def _name_tokens(name: str) -> set[str]:
    parts = re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", name)
    return {p.lower() for p in parts if p}


def _singular(t: str) -> str:
    return t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t


def rank_candidates(
    root: Path,
    files: list[str],
    index: SymbolIndex,
    issue: str,
    *,
    top_k: int = 8,
) -> tuple[list[CandidateFile], list[str]]:
    terms, strong = extract_issue_terms(issue)
    if not terms:
        return [], []
    py_files = [f for f in files if f.endswith(".py")]
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    def add(path: str, pts: float, why: str) -> None:
        scores[path] = scores.get(path, 0.0) + pts
        bucket = reasons.setdefault(path, [])
        if why not in bucket and len(bucket) < 6:
            bucket.append(why)

    n_files = max(len(py_files), 1)
    for term in terms:
        t_low = term.lower()
        t_norm = _singular(t_low.split(".")[-1])
        is_strong = term in strong

        # symbol-name matches
        for sym in index.all_symbols():
            if sym.name == term.split(".")[-1]:
                add(sym.path, 4.0, f"defines `{sym.qualname}` (named in issue)")
            elif t_norm in {_singular(x) for x in _name_tokens(sym.name)}:
                add(sym.path, 1.5 if is_strong else 1.0, f"symbol `{sym.qualname}` ~ '{term}'")

        # path matches
        for f in py_files:
            parts = PurePosixPath(f).with_suffix("").parts
            toks = {_singular(tok) for p in parts for tok in _name_tokens(p)}
            if t_norm in toks:
                add(f, 2.0, f"path matches '{term}'")

        # content matches, IDF-weighted so common words matter less
        try:
            res = ripgrep(
                root,
                term.split(".")[-1],
                fixed_string=True,
                word=True,
                ignore_case=not is_strong,
                glob="*.py",
                max_results=400,
            )
        except SearchError:
            continue
        per_file = Counter(h.path for h in res.hits)
        if not per_file:
            continue
        idf = math.log(1 + n_files / len(per_file))
        for path, count in per_file.items():
            add(path, min(count, 5) * 0.3 * idf, f"mentions '{term}' x{count}")

    ranked: list[CandidateFile] = []
    for path, raw in scores.items():
        score = raw * 0.5 if is_test_path(path) else raw
        ranked.append(CandidateFile(path=path, score=round(score, 2), reasons=reasons[path]))
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked[:top_k], terms


_ENTRY_PATTERNS = [
    (r"\bFastAPI\(", "FastAPI app"),
    (r"\bAPIRouter\(", "FastAPI router"),
    (r"\bFlask\(", "Flask app"),
    (r"if __name__ == ['\"]__main__['\"]", "__main__ block"),
    (r"@click\.(command|group)", "click CLI"),
    (r"argparse\.ArgumentParser\(", "argparse CLI"),
]

_MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "tox.ini",
    "pytest.ini",
    "conftest.py",
)


def build_summary(
    root: Path, files: list[str], index: SymbolIndex, issue: str, *, top_k: int = 8
) -> RepoSummary:
    py = [f for f in files if f.endswith(".py")]
    top_level = sorted({PurePosixPath(f).parts[0] + ("/" if "/" in f else "") for f in files})
    packages = sorted(
        {str(PurePosixPath(f).parent) for f in py if PurePosixPath(f).name == "__init__.py"}
    )
    test_dirs = sorted(
        {
            str(PurePosixPath(f).parent)
            for f in py
            if is_test_path(f) and str(PurePosixPath(f).parent) != "."
        }
    )
    manifests = [f for f in files if PurePosixPath(f).name in _MANIFESTS]

    framework: Literal["pytest", "unittest", "unknown"] = "unknown"
    try:
        if (
            any(PurePosixPath(f).name == "conftest.py" for f in files)
            or ripgrep(root, r"^\s*(import pytest|from pytest)", glob="*.py", max_results=1).hits
        ):
            framework = "pytest"
        elif ripgrep(root, r"^\s*import unittest", glob="*.py", max_results=1).hits:
            framework = "unittest"
    except SearchError:
        pass
    if framework == "unknown":
        for m in manifests:
            try:
                if "pytest" in (root / m).read_text(encoding="utf-8", errors="ignore"):
                    framework = "pytest"
                    break
            except OSError:
                continue

    entry_points: list[str] = []
    for pattern, label in _ENTRY_PATTERNS:
        try:
            res = ripgrep(root, pattern, glob="*.py", max_results=5)
        except SearchError:
            continue
        for h in res.hits:
            if not is_test_path(h.path):
                entry_points.append(f"{h.path}:{h.line} ({label})")

    candidates, terms = rank_candidates(root, files, index, issue, top_k=top_k)
    return RepoSummary(
        file_count=len(files),
        python_file_count=len(py),
        top_level=top_level[:40],
        packages=packages[:40],
        test_dirs=test_dirs[:20],
        test_framework=framework,
        manifests=manifests,
        entry_points=entry_points[:10],
        candidates=candidates,
        issue_terms=terms,
    )
