"""Path jail: every model-supplied path is resolved and checked before any file access.

Rules
- Paths are repository-relative. Absolute paths, `..` escapes, NUL bytes are rejected.
- The fully resolved path (symlinks followed) must stay inside the workspace root.
- `.git` internals are never readable or writable through tools.
- Writes have an additional policy (see `WritePolicy`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from swe_agent.core.errors import PathPolicyError

_ALWAYS_FORBIDDEN_PARTS = {".git"}


@dataclass(frozen=True)
class PathJail:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(os.path.realpath(self.root)))

    def resolve(self, rel: str, *, must_exist: bool = False) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise PathPolicyError("path must be a non-empty string")
        if "\x00" in rel:
            raise PathPolicyError("path contains NUL byte")
        rel = rel.strip().replace("\\", "/")
        pure = PurePosixPath(rel)
        if pure.is_absolute():
            raise PathPolicyError(f"absolute paths are not allowed: {rel!r}")
        if any(part == ".." for part in pure.parts):
            raise PathPolicyError(f"parent-directory segments are not allowed: {rel!r}")
        if any(part in _ALWAYS_FORBIDDEN_PARTS for part in pure.parts):
            raise PathPolicyError(f"access to {rel!r} is forbidden")
        candidate = self.root.joinpath(*pure.parts) if pure.parts != (".",) else self.root
        # realpath follows symlinks, including a symlinked parent directory.
        real = Path(os.path.realpath(candidate))
        if real != self.root and self.root not in real.parents:
            raise PathPolicyError(f"path escapes the workspace: {rel!r}")
        if must_exist and not real.exists():
            raise PathPolicyError(f"no such file or directory: {rel!r}")
        return real

    def relative(self, abs_path: Path) -> str:
        return Path(os.path.realpath(abs_path)).relative_to(self.root).as_posix() or "."


TEST_DIR_NAMES = {"tests", "test", "testing"}
PROTECTED_FILENAMES = {
    "conftest.py",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "tox.ini",
    "pytest.ini",
    "noxfile.py",
    "requirements.txt",
    "Makefile",
}
PROTECTED_DIR_PREFIXES = (".github/", ".gitlab/", ".circleci/")


def is_test_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    name = p.name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return any(part in TEST_DIR_NAMES for part in p.parts[:-1])


@dataclass(frozen=True)
class WritePolicy:
    """Which files a node may create or modify.

    implement:  source files only (no tests, no build/CI/config files)
    reproduce:  may create new test files only
    """

    allow_source: bool = True
    allow_new_tests: bool = False
    allow_existing_tests: bool = False
    extra_forbidden: frozenset[str] = field(default_factory=frozenset)
    # Specific new test files that may appear in the patch (the reproduction test).
    allowed_new_tests: frozenset[str] = field(default_factory=frozenset)

    def check(self, rel: str, *, exists: bool) -> None:
        p = PurePosixPath(rel)
        if p.name in PROTECTED_FILENAMES or p.name.startswith("requirements"):
            raise PathPolicyError(f"{rel!r} is a protected build/test-config file")
        if rel.startswith(PROTECTED_DIR_PREFIXES):
            raise PathPolicyError(f"{rel!r} is in a protected CI directory")
        if rel in self.extra_forbidden:
            raise PathPolicyError(f"{rel!r} is forbidden for this node")
        if is_test_path(rel):
            if not exists and rel in self.allowed_new_tests:
                return
            if exists and not self.allow_existing_tests:
                raise PathPolicyError(f"existing test files are read-only: {rel!r}")
            if not exists and not self.allow_new_tests:
                raise PathPolicyError(f"this node may not create test files: {rel!r}")
            return
        if not self.allow_source:
            raise PathPolicyError(f"this node may not modify source files: {rel!r}")
